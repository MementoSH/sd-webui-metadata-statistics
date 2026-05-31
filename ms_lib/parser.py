"""Parse PNG generation metadata into normalized tag / LoRA / model fields.

Notes:
- Tag normalization: strip whitespace, lower-case, convert underscores to
  spaces, collapse internal whitespace.
- Wrapping parens/brackets and `:weight` suffixes are stripped from each token
  so that `(blue_eyes:1.2)` and `blue eyes` count as the same tag.
- LoRA references like `<lora:name:0.8>` are extracted as LoRA `name` and
  removed from the regular tag list.
- The `Lora hashes` infotext field also contributes LoRA names.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from PIL import Image

_LORA_RE = re.compile(r"<lora:([^:>\s]+)(?::[^>]*)?>", re.IGNORECASE)
_LYCO_RE = re.compile(r"<lyco:([^:>\s]+)(?::[^>]*)?>", re.IGNORECASE)
# Catch-all for any `<...>` segment that survives the strict regexes
# (whitespace inside, other extension prefixes, etc.). We separate it from
# surrounding text so a stray angle-bracketed token can never merge with
# adjacent tags.
_ANGLE_RE = re.compile(r"<([^>]*)>")
_WEIGHT_TAIL_RE = re.compile(r":\s*-?\d+(?:\.\d+)?\s*$")
_WS_RE = re.compile(r"\s+")
_BREAK_TOKENS = {"break", "and"}
_OPEN = "([{"
_CLOSE = ")]}"
_PAIRS = {")": "(", "]": "[", "}": "{"}


@dataclass
class ParsedMetadata:
    parse_ok: bool
    positive: List[str] = field(default_factory=list)
    negative: List[str] = field(default_factory=list)
    loras: List[str] = field(default_factory=list)
    model: Optional[str] = None
    raw_parameters: Optional[str] = None


def _strip_brackets(token: str) -> str:
    """Strip balanced wrapping ()/[]/{}, repeatedly."""
    changed = True
    while changed and len(token) >= 2:
        changed = False
        first, last = token[0], token[-1]
        if (first, last) in (("(", ")"), ("[", "]"), ("{", "}")):
            token = token[1:-1].strip()
            changed = True
    return token


def normalize_tag(token: str) -> str:
    t = token.strip()
    if not t:
        return ""
    t = _strip_brackets(t)
    # Remove trailing :weight if present
    m = _WEIGHT_TAIL_RE.search(t)
    if m:
        t = t[: m.start()].rstrip()
    t = _strip_brackets(t)
    t = t.lower()
    t = t.replace("_", " ")
    t = _WS_RE.sub(" ", t).strip()
    if t in _BREAK_TOKENS:
        return ""
    return t


def _extract_loras(text: str) -> (str, List[str]):
    """Pull LoRA names out of the prompt and replace each angle-bracketed
    token with a comma so it can never merge with adjacent tags.

    Two passes:
    1. Strict `<lora:name:...>` / `<lyco:name:...>` regexes — fast and exact.
    2. Catch-all `<...>` regex for anything left (loose whitespace, other
       extension syntax like `<hypernet:...>`). If a leftover segment also
       carries a `lora:` / `lyco:` prefix it contributes a name; otherwise it
       is just stripped.
    """
    names: List[str] = []
    for rx in (_LORA_RE, _LYCO_RE):
        for m in rx.finditer(text):
            names.append(m.group(1).strip())
        text = rx.sub(", ", text)

    # Second pass: catch any remaining `<...>` segment.
    def _angle_repl(m: re.Match) -> str:
        inner = (m.group(1) or "").strip()
        if ":" in inner:
            kind, _, rest = inner.partition(":")
            if kind.strip().lower() in ("lora", "lyco"):
                name = rest.partition(":")[0].strip()
                if name and name not in names:
                    names.append(name)
        return ", "

    text = _ANGLE_RE.sub(_angle_repl, text)
    return text, names


def _strip_paren_groups(text: str) -> str:
    """Repeatedly replace each innermost (...)/[...]/{...} group with its inner
    content, dropping a trailing `:weight` just before the closer. This handles
    A1111 grouped-weight syntax like `(a, b, c:1.2)` so that `a`, `b`, `c` later
    split out as three separate tags.
    """
    # Find innermost groups: an opener followed by content with no other openers,
    # then the matching closer. Repeat until no parens remain.
    inner_re = re.compile(r"\(([^()\[\]{}]*)\)|\[([^()\[\]{}]*)\]|\{([^()\[\]{}]*)\}")
    prev = None
    out = text
    # Guard against pathological loops; in practice depth is small.
    for _ in range(20):
        if prev == out:
            break
        prev = out

        def repl(m: re.Match) -> str:
            inner = m.group(1) or m.group(2) or m.group(3) or ""
            # Strip a trailing :weight from the inner content
            wm = _WEIGHT_TAIL_RE.search(inner)
            if wm:
                inner = inner[: wm.start()].rstrip()
            return inner

        out = inner_re.sub(repl, out)
    return out


def _split_and_normalize(text: str) -> List[str]:
    if not text:
        return []
    text = _strip_paren_groups(text)
    tokens = []
    seen = set()
    for raw in text.split(","):
        n = normalize_tag(raw)
        if not n:
            continue
        if n in seen:
            # Each tag counts once per image regardless of repetitions
            continue
        seen.add(n)
        tokens.append(n)
    return tokens


def _parse_lora_hashes_field(value: str) -> List[str]:
    """Lora hashes field looks like: name1: hash, name2: hash (sometimes wrapped in quotes)."""
    if not value:
        return []
    v = value.strip().strip('"').strip("'")
    out: List[str] = []
    for part in v.split(","):
        part = part.strip()
        if not part:
            continue
        name = part.split(":", 1)[0].strip()
        if name:
            out.append(name)
    return out


def _read_png_text(image_path: str) -> Optional[str]:
    """Return the raw 'parameters' (or equivalent) text chunk, or None."""
    try:
        with Image.open(image_path) as img:
            info = dict(img.info)
    except Exception:
        return None

    for key in ("parameters", "Parameters", "prompt", "Comment", "Description"):
        v = info.get(key)
        if v:
            if isinstance(v, bytes):
                try:
                    v = v.decode("utf-8", errors="replace")
                except Exception:
                    continue
            return str(v)
    return None


def _parse_with_webui(raw: str) -> Optional[dict]:
    try:
        from modules.infotext_utils import parse_generation_parameters  # type: ignore
        return parse_generation_parameters(raw, skip_fields=[])
    except Exception:
        pass
    try:
        from modules.generation_parameters_copypaste import (  # type: ignore
            parse_generation_parameters,
        )
        return parse_generation_parameters(raw, skip_fields=[])
    except Exception:
        return None


def _fallback_parse(raw: str) -> dict:
    """Minimal parser used if WebUI's parser is unavailable."""
    res = {"Prompt": "", "Negative prompt": ""}
    text = raw.strip()
    if not text:
        return res
    lines = text.split("\n")
    # Last line is the key:value params line if it contains at least one ': '
    last = lines[-1] if lines else ""
    body_lines = lines[:-1] if ": " in last and "," in last else lines
    if body_lines is lines:
        last = ""

    in_neg = False
    pos_parts: List[str] = []
    neg_parts: List[str] = []
    for line in body_lines:
        stripped = line.strip()
        if stripped.startswith("Negative prompt:"):
            in_neg = True
            stripped = stripped[len("Negative prompt:"):].strip()
        (neg_parts if in_neg else pos_parts).append(stripped)
    res["Prompt"] = "\n".join(p for p in pos_parts if p)
    res["Negative prompt"] = "\n".join(p for p in neg_parts if p)

    if last:
        # Naive key:value, comma-separated parsing
        for chunk in re.split(r",\s*(?=[A-Z][A-Za-z0-9 _\-]+:\s)", last):
            if ":" in chunk:
                k, v = chunk.split(":", 1)
                res[k.strip()] = v.strip().strip('"')
    return res


def parse_file(image_path: str) -> ParsedMetadata:
    raw = _read_png_text(image_path)
    if not raw:
        return ParsedMetadata(parse_ok=False)

    parsed = _parse_with_webui(raw) or _fallback_parse(raw)

    pos_text = str(parsed.get("Prompt", "") or "")
    neg_text = str(parsed.get("Negative prompt", "") or "")

    pos_text, pos_loras = _extract_loras(pos_text)
    neg_text, neg_loras = _extract_loras(neg_text)

    positive = _split_and_normalize(pos_text)
    negative = _split_and_normalize(neg_text)

    loras = list(dict.fromkeys(pos_loras + neg_loras))
    for field_name in ("Lora hashes", "TI hashes", "Lora"):
        v = parsed.get(field_name)
        if v:
            for n in _parse_lora_hashes_field(str(v)):
                if n not in loras:
                    loras.append(n)

    model = parsed.get("Model") or parsed.get("model") or None
    if isinstance(model, str):
        model = model.strip() or None

    return ParsedMetadata(
        parse_ok=True,
        positive=positive,
        negative=negative,
        loras=loras,
        model=model,
        raw_parameters=raw,
    )


def file_stat(path: str) -> Optional[os.stat_result]:
    try:
        return os.stat(path)
    except OSError:
        return None

"""Resolve the outputs directory and the extension's data folders."""

from __future__ import annotations

import os
from typing import List

EXT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(EXT_ROOT, "data")
THUMB_DIR = os.path.join(DATA_DIR, "thumbs")
DB_PATH = os.path.join(DATA_DIR, "metadata.db")


def ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)


def _resolve(opt_value: str, fallback: str) -> str:
    if not opt_value:
        return fallback
    if os.path.isabs(opt_value):
        return opt_value
    # WebUI stores paths relative to its working dir (the `webui/` folder)
    return os.path.abspath(opt_value)


def outputs_roots() -> List[str]:
    """Return the list of directories that should be scanned.

    Reads WebUI's configured output paths. If `outdir_samples` is set, that
    single folder is used. Otherwise the three per-mode folders are used.
    Falls back to <webui>/outputs/* if `shared.opts` is not yet available.
    """
    try:
        from modules import shared  # type: ignore
        opts = shared.opts
    except Exception:
        opts = None

    # Fallback root: <webui>/outputs/
    webui_dir = os.path.dirname(os.path.dirname(os.path.dirname(EXT_ROOT)))
    default_outputs = os.path.join(webui_dir, "outputs")

    if opts is None:
        return [default_outputs]

    shared_root = getattr(opts, "outdir_samples", "") or ""
    if shared_root:
        return [_resolve(shared_root, default_outputs)]

    sub_keys = [
        "outdir_txt2img_samples",
        "outdir_img2img_samples",
        "outdir_extras_samples",
        "outdir_txt2img_grids",
        "outdir_img2img_grids",
        "outdir_save",
        "outdir_init_images",
    ]
    roots: List[str] = []
    for k in sub_keys:
        v = getattr(opts, k, "") or ""
        if v:
            roots.append(_resolve(v, default_outputs))
    # Deduplicate while preserving order
    seen = set()
    unique: List[str] = []
    for r in roots:
        n = os.path.normcase(os.path.normpath(r))
        if n in seen:
            continue
        seen.add(n)
        unique.append(r)
    return unique or [default_outputs]


def thumb_path_for(image_id: int) -> str:
    # Two-level sharding so a single folder doesn't grow unbounded
    s = f"{image_id:08d}"
    sub = os.path.join(THUMB_DIR, s[:2], s[2:4])
    os.makedirs(sub, exist_ok=True)
    return os.path.join(sub, f"{s}.jpg")

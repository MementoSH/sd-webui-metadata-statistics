"""Search images by tags using AND/OR/NOT logic.

Two entry points:
- simple_search(and_terms, or_terms, not_terms, ...)
- expert_search(query_string, ...)

Both share the same underlying SQL builder. The expert parser supports
parenthesized expressions with AND / OR / NOT operators. Tag terms are
normalized the same way as during indexing.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Tuple

from . import db
from .parser import normalize_tag


# ---------- expression tree ----------

@dataclass
class Term:
    tag: str

@dataclass
class Not:
    child: object

@dataclass
class And:
    children: list

@dataclass
class Or:
    children: list


def _split_terms(text: str) -> List[str]:
    if not text:
        return []
    out: List[str] = []
    for raw in text.split(","):
        n = normalize_tag(raw)
        if n:
            out.append(n)
    return out


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _node_to_sql(node, params: list, *, partial: bool) -> str:
    if isinstance(node, Term):
        if partial:
            params.append(f"%{_like_escape(node.tag)}%")
            return (
                "i.id IN ("
                " SELECT it.image_id FROM image_tags it "
                " JOIN tags t ON t.id = it.tag_id "
                " WHERE t.normalized LIKE ? ESCAPE '\\'"
                ")"
            )
        params.append(node.tag)
        return (
            "i.id IN ("
            " SELECT it.image_id FROM image_tags it "
            " JOIN tags t ON t.id = it.tag_id WHERE t.normalized = ?"
            ")"
        )
    if isinstance(node, Not):
        return "NOT (" + _node_to_sql(node.child, params, partial=partial) + ")"
    if isinstance(node, And):
        if not node.children:
            return "1=1"
        return "(" + " AND ".join(_node_to_sql(c, params, partial=partial) for c in node.children) + ")"
    if isinstance(node, Or):
        if not node.children:
            return "1=0"
        return "(" + " OR ".join(_node_to_sql(c, params, partial=partial) for c in node.children) + ")"
    raise ValueError(f"unknown node: {node!r}")


# ---------- expert query parser ----------

_TOKEN_RE = re.compile(
    r"\s*(?:(?P<lparen>\()|(?P<rparen>\))|"
    r"(?P<quoted>\"[^\"]*\"|'[^']*')|"
    r"(?P<word>[^\s()]+))\s*"
)
_OPS = {"AND", "OR", "NOT"}


def _tokenize(query: str) -> List[str]:
    out: List[str] = []
    pos = 0
    while pos < len(query):
        m = _TOKEN_RE.match(query, pos)
        if not m:
            raise ValueError(f"unexpected character at position {pos}")
        if m.group("lparen"):
            out.append("(")
        elif m.group("rparen"):
            out.append(")")
        elif m.group("quoted"):
            out.append(m.group("quoted")[1:-1])
        else:
            out.append(m.group("word"))
        pos = m.end()
    return out


class _Parser:
    """Grammar:
        expr   := or_expr
        or_expr := and_expr (OR and_expr)*
        and_expr := not_expr ((AND)? not_expr)*    # implicit AND when omitted
        not_expr := NOT not_expr | atom
        atom   := '(' expr ')' | TERM
    """

    def __init__(self, tokens: List[str]):
        self.tokens = tokens
        self.i = 0

    def peek(self) -> Optional[str]:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def eat(self) -> str:
        t = self.tokens[self.i]
        self.i += 1
        return t

    def parse(self):
        node = self.parse_or()
        if self.i != len(self.tokens):
            raise ValueError(f"unexpected token: {self.tokens[self.i]!r}")
        return node

    def parse_or(self):
        left = self.parse_and()
        if self.peek() and self.peek().upper() == "OR":
            children = [left]
            while self.peek() and self.peek().upper() == "OR":
                self.eat()
                children.append(self.parse_and())
            return Or(children)
        return left

    def parse_and(self):
        left = self.parse_not()
        children = [left]
        while True:
            t = self.peek()
            if t is None:
                break
            if t.upper() == "OR" or t == ")":
                break
            if t.upper() == "AND":
                self.eat()
                children.append(self.parse_not())
                continue
            # implicit AND
            children.append(self.parse_not())
        return And(children) if len(children) > 1 else left

    def parse_not(self):
        t = self.peek()
        if t and t.upper() == "NOT":
            self.eat()
            return Not(self.parse_not())
        return self.parse_atom()

    def parse_atom(self):
        t = self.eat()
        if t == "(":
            inner = self.parse_or()
            if self.peek() != ")":
                raise ValueError("missing closing ')'")
            self.eat()
            return inner
        if t.upper() in _OPS:
            raise ValueError(f"unexpected operator: {t}")
        n = normalize_tag(t)
        if not n:
            # treat as an unsatisfiable term
            return And([])  # always true; but a stray empty term is harmless
        return Term(n)


def parse_expert_query(query: str):
    tokens = _tokenize(query.strip())
    if not tokens:
        return And([])  # match-all
    return _Parser(tokens).parse()


# ---------- public API ----------

@dataclass
class SearchResult:
    total: int
    image_ids: List[int]
    page: int
    page_size: int


def _build_root_node_simple(and_t, or_t, not_t):
    nodes = []
    if and_t:
        nodes.append(And([Term(t) for t in and_t]))
    if or_t:
        nodes.append(Or([Term(t) for t in or_t]))
    if not_t:
        nodes.append(Not(Or([Term(t) for t in not_t])))
    if not nodes:
        return And([])
    return And(nodes) if len(nodes) > 1 else nodes[0]


CATEGORY_MODES = ("any", "in", "missing")


def _category_clause(category_id: Optional[int], category_mode: str, params: list) -> str:
    """Return an extra WHERE fragment to AND into the main query, or empty."""
    if category_id is None or category_mode == "any":
        return ""
    if category_mode == "in":
        params.append(int(category_id))
        return (
            " AND EXISTS (SELECT 1 FROM image_categories ic "
            "WHERE ic.image_id = i.id AND ic.category_id = ?)"
        )
    if category_mode == "missing":
        params.append(int(category_id))
        return (
            " AND NOT EXISTS (SELECT 1 FROM image_categories ic "
            "WHERE ic.image_id = i.id AND ic.category_id = ?)"
        )
    raise ValueError(f"unknown category_mode: {category_mode!r}")


def _run(
    root,
    page: int,
    page_size: int,
    *,
    partial: bool,
    category_id: Optional[int] = None,
    category_mode: str = "any",
) -> SearchResult:
    params: list = []
    where = _node_to_sql(root, params, partial=partial)
    cat_clause = _category_clause(category_id, category_mode, params)
    page = max(1, int(page))
    page_size = max(1, int(page_size))
    offset = (page - 1) * page_size

    with db.connect() as con:
        total = con.execute(
            f"SELECT COUNT(*) AS n FROM images i WHERE {where}{cat_clause}",
            params,
        ).fetchone()["n"]
        rows = con.execute(
            f"SELECT i.id FROM images i WHERE {where}{cat_clause} "
            f"ORDER BY i.mtime DESC, i.id DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
    return SearchResult(
        total=int(total),
        image_ids=[int(r["id"]) for r in rows],
        page=page,
        page_size=page_size,
    )


def simple_search(
    and_text: str,
    or_text: str,
    not_text: str,
    *,
    page: int = 1,
    page_size: int = 60,
    partial: bool = True,
    category_id: Optional[int] = None,
    category_mode: str = "any",
) -> SearchResult:
    and_t = _split_terms(and_text)
    or_t = _split_terms(or_text)
    not_t = _split_terms(not_text)
    root = _build_root_node_simple(and_t, or_t, not_t)
    return _run(
        root, page, page_size,
        partial=partial, category_id=category_id, category_mode=category_mode,
    )


def expert_search(
    query: str,
    *,
    page: int = 1,
    page_size: int = 60,
    partial: bool = True,
    category_id: Optional[int] = None,
    category_mode: str = "any",
) -> SearchResult:
    root = parse_expert_query(query or "")
    return _run(
        root, page, page_size,
        partial=partial, category_id=category_id, category_mode=category_mode,
    )


# ---------- helpers used by UI ----------

def get_image_details(image_id: int) -> Optional[dict]:
    with db.connect() as con:
        row = con.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        if not row:
            return None
        pos = [r["normalized"] for r in con.execute(
            "SELECT t.normalized FROM image_tags it "
            "JOIN tags t ON t.id=it.tag_id "
            "WHERE it.image_id=? AND it.kind=? ORDER BY t.normalized",
            (image_id, db.KIND_POS),
        ).fetchall()]
        neg = [r["normalized"] for r in con.execute(
            "SELECT t.normalized FROM image_tags it "
            "JOIN tags t ON t.id=it.tag_id "
            "WHERE it.image_id=? AND it.kind=? ORDER BY t.normalized",
            (image_id, db.KIND_NEG),
        ).fetchall()]
        loras = [r["name"] for r in con.execute(
            "SELECT l.name FROM image_loras il "
            "JOIN loras l ON l.id=il.lora_id "
            "WHERE il.image_id=? ORDER BY l.name",
            (image_id,),
        ).fetchall()]
        cats = [r["name"] for r in con.execute(
            "SELECT c.name FROM image_categories ic "
            "JOIN categories c ON c.id=ic.category_id "
            "WHERE ic.image_id=? ORDER BY c.name",
            (image_id,),
        ).fetchall()]
    return {
        "id": int(row["id"]),
        "path": row["path"],
        "model": row["model"],
        "positive": pos,
        "negative": neg,
        "loras": loras,
        "categories": cats,
    }


def ranked(kind: str, limit: int = 200) -> List[Tuple[str, int]]:
    """Return ranked frequency lists.

    kind in {'pos', 'neg', 'lora', 'model'}.
    """
    with db.connect() as con:
        if kind == "pos":
            rows = con.execute(
                "SELECT t.normalized AS name, COUNT(*) AS n "
                "FROM image_tags it JOIN tags t ON t.id=it.tag_id "
                "WHERE it.kind=? GROUP BY t.id ORDER BY n DESC, name ASC LIMIT ?",
                (db.KIND_POS, limit),
            ).fetchall()
        elif kind == "neg":
            rows = con.execute(
                "SELECT t.normalized AS name, COUNT(*) AS n "
                "FROM image_tags it JOIN tags t ON t.id=it.tag_id "
                "WHERE it.kind=? GROUP BY t.id ORDER BY n DESC, name ASC LIMIT ?",
                (db.KIND_NEG, limit),
            ).fetchall()
        elif kind == "lora":
            rows = con.execute(
                "SELECT l.name AS name, COUNT(*) AS n "
                "FROM image_loras il JOIN loras l ON l.id=il.lora_id "
                "GROUP BY l.id ORDER BY n DESC, name ASC LIMIT ?",
                (limit,),
            ).fetchall()
        elif kind == "model":
            rows = con.execute(
                "SELECT COALESCE(model,'(unknown)') AS name, COUNT(*) AS n "
                "FROM images GROUP BY model ORDER BY n DESC, name ASC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            return []
    return [(r["name"], int(r["n"])) for r in rows]

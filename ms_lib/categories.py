"""Category management.

A category is a label like 'Characters', 'Scene', 'Lighting'. Tags can be
assigned to any number of categories. Whenever the tag<->category mapping
changes, the materialized image_categories table is updated for the affected
categories so search/queries stay correct without scanning images each time.
"""

from __future__ import annotations

from typing import List, Tuple

from . import db
from .parser import normalize_tag


# ---------- category CRUD ----------

def create_category(name: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Category name cannot be empty.")
    with db.connect() as con:
        row = con.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()
        if row:
            return int(row["id"])
        cur = con.execute("INSERT INTO categories(name) VALUES (?)", (name,))
        return int(cur.lastrowid)


def rename_category(category_id: int, new_name: str) -> None:
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("Category name cannot be empty.")
    with db.connect() as con:
        con.execute("UPDATE categories SET name=? WHERE id=?", (new_name, category_id))


def delete_category(category_id: int) -> None:
    with db.connect() as con:
        con.execute("DELETE FROM categories WHERE id=?", (category_id,))
        # image_categories rows for it are removed by ON DELETE CASCADE


def list_categories() -> List[Tuple[int, str]]:
    with db.connect() as con:
        rows = con.execute(
            "SELECT id, name FROM categories ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [(int(r["id"]), r["name"]) for r in rows]


def category_image_count(category_id: int) -> int:
    with db.connect() as con:
        row = con.execute(
            "SELECT COUNT(*) AS n FROM image_categories WHERE category_id=?",
            (category_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


# ---------- tag <-> category ----------

def _ensure_tag(con, normalized: str) -> int:
    row = con.execute("SELECT id FROM tags WHERE normalized=?", (normalized,)).fetchone()
    if row:
        return int(row["id"])
    cur = con.execute("INSERT INTO tags(normalized) VALUES (?)", (normalized,))
    return int(cur.lastrowid)


def assign_tag_to_category(tag_text: str, category_id: int) -> Tuple[bool, str]:
    """Assign a tag (by raw text, will be normalized) to a category. Returns
    (changed, normalized_tag)."""
    n = normalize_tag(tag_text)
    if not n:
        return (False, "")
    with db.connect() as con:
        tag_id = _ensure_tag(con, n)
        cur = con.execute(
            "INSERT OR IGNORE INTO tag_categories(tag_id, category_id) VALUES (?,?)",
            (tag_id, category_id),
        )
        changed = cur.rowcount > 0
        if changed:
            db.rematerialize_categories_for_category(con, category_id)
    return (changed, n)


def unassign_tag_from_category(tag_text: str, category_id: int) -> bool:
    n = normalize_tag(tag_text)
    if not n:
        return False
    with db.connect() as con:
        row = con.execute("SELECT id FROM tags WHERE normalized=?", (n,)).fetchone()
        if not row:
            return False
        tag_id = int(row["id"])
        cur = con.execute(
            "DELETE FROM tag_categories WHERE tag_id=? AND category_id=?",
            (tag_id, category_id),
        )
        changed = cur.rowcount > 0
        if changed:
            db.rematerialize_categories_for_category(con, category_id)
    return changed


def tags_in_category(category_id: int) -> List[str]:
    with db.connect() as con:
        rows = con.execute(
            "SELECT t.normalized FROM tag_categories tc "
            "JOIN tags t ON t.id=tc.tag_id "
            "WHERE tc.category_id=? ORDER BY t.normalized",
            (category_id,),
        ).fetchall()
    return [r["normalized"] for r in rows]


def categories_for_tag(tag_text: str) -> List[Tuple[int, str]]:
    n = normalize_tag(tag_text)
    if not n:
        return []
    with db.connect() as con:
        rows = con.execute(
            "SELECT c.id, c.name FROM tag_categories tc "
            "JOIN categories c ON c.id=tc.category_id "
            "JOIN tags t ON t.id=tc.tag_id "
            "WHERE t.normalized=? ORDER BY c.name",
            (n,),
        ).fetchall()
    return [(int(r["id"]), r["name"]) for r in rows]


# ---------- lora <-> category ----------
# LoRA names are stored verbatim (case-preserving) in the `loras` table; we
# match case-insensitively here so users don't have to remember exact casing.

def _find_lora_id(con, name: str) -> int:
    name = name.strip()
    row = con.execute(
        "SELECT id FROM loras WHERE name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = con.execute("INSERT INTO loras(name) VALUES (?)", (name,))
    return int(cur.lastrowid)


def assign_lora_to_category(lora_name: str, category_id: int) -> Tuple[bool, str]:
    name = (lora_name or "").strip()
    if not name:
        return (False, "")
    with db.connect() as con:
        lora_id = _find_lora_id(con, name)
        # Use the canonical stored name (case as first inserted) for display
        row = con.execute("SELECT name FROM loras WHERE id=?", (lora_id,)).fetchone()
        canonical = row["name"] if row else name
        cur = con.execute(
            "INSERT OR IGNORE INTO lora_categories(lora_id, category_id) VALUES (?,?)",
            (lora_id, category_id),
        )
        changed = cur.rowcount > 0
        if changed:
            db.rematerialize_categories_for_category(con, category_id)
    return (changed, canonical)


def unassign_lora_from_category(lora_name: str, category_id: int) -> bool:
    name = (lora_name or "").strip()
    if not name:
        return False
    with db.connect() as con:
        row = con.execute(
            "SELECT id FROM loras WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if not row:
            return False
        lora_id = int(row["id"])
        cur = con.execute(
            "DELETE FROM lora_categories WHERE lora_id=? AND category_id=?",
            (lora_id, category_id),
        )
        changed = cur.rowcount > 0
        if changed:
            db.rematerialize_categories_for_category(con, category_id)
    return changed


def loras_in_category(category_id: int) -> List[str]:
    with db.connect() as con:
        rows = con.execute(
            "SELECT l.name FROM lora_categories lc "
            "JOIN loras l ON l.id=lc.lora_id "
            "WHERE lc.category_id=? ORDER BY l.name COLLATE NOCASE",
            (category_id,),
        ).fetchall()
    return [r["name"] for r in rows]

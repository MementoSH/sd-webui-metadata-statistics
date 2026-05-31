"""SQLite database layer for the Metadata Statistics extension."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterable, Iterator, Optional

from . import paths

_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    path      TEXT NOT NULL UNIQUE,
    mtime     REAL NOT NULL,
    size      INTEGER NOT NULL,
    model     TEXT,
    parse_ok  INTEGER NOT NULL DEFAULT 0,
    indexed_at REAL NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_images_model ON images(model);

CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized TEXT NOT NULL UNIQUE
);

-- kind: 0 = positive, 1 = negative
CREATE TABLE IF NOT EXISTS image_tags (
    image_id INTEGER NOT NULL,
    tag_id   INTEGER NOT NULL,
    kind     INTEGER NOT NULL,
    PRIMARY KEY (image_id, tag_id, kind),
    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id)   REFERENCES tags(id)   ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_image_tags_tag  ON image_tags(tag_id, kind);
CREATE INDEX IF NOT EXISTS idx_image_tags_kind ON image_tags(kind);

CREATE TABLE IF NOT EXISTS loras (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS image_loras (
    image_id INTEGER NOT NULL,
    lora_id  INTEGER NOT NULL,
    PRIMARY KEY (image_id, lora_id),
    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
    FOREIGN KEY (lora_id)  REFERENCES loras(id)  ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_image_loras_lora ON image_loras(lora_id);

CREATE TABLE IF NOT EXISTS categories (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS tag_categories (
    tag_id      INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    PRIMARY KEY (tag_id, category_id),
    FOREIGN KEY (tag_id)      REFERENCES tags(id)       ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tag_categories_cat ON tag_categories(category_id);

CREATE TABLE IF NOT EXISTS lora_categories (
    lora_id     INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    PRIMARY KEY (lora_id, category_id),
    FOREIGN KEY (lora_id)     REFERENCES loras(id)      ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_lora_categories_cat ON lora_categories(category_id);

CREATE TABLE IF NOT EXISTS image_categories (
    image_id    INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    PRIMARY KEY (image_id, category_id),
    FOREIGN KEY (image_id)    REFERENCES images(id)     ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_image_categories_cat ON image_categories(category_id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

KIND_POS = 0
KIND_NEG = 1

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    paths.ensure_dirs()
    con = sqlite3.connect(paths.DB_PATH, timeout=30, isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.row_factory = sqlite3.Row
    return con


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Yield a serialized connection. We keep one writer at a time."""
    with _lock:
        con = _connect()
        try:
            yield con
        finally:
            con.close()


def init_db() -> None:
    with connect() as con:
        con.executescript(_SCHEMA)


# ---------- image upsert ----------

def get_image_by_path(con: sqlite3.Connection, path: str) -> Optional[sqlite3.Row]:
    return con.execute("SELECT * FROM images WHERE path = ?", (path,)).fetchone()


def upsert_image(
    con: sqlite3.Connection,
    *,
    path: str,
    mtime: float,
    size: int,
    model: Optional[str],
    parse_ok: bool,
) -> int:
    row = get_image_by_path(con, path)
    if row is None:
        cur = con.execute(
            "INSERT INTO images(path, mtime, size, model, parse_ok) VALUES (?,?,?,?,?)",
            (path, mtime, size, model, 1 if parse_ok else 0),
        )
        return int(cur.lastrowid)
    image_id = int(row["id"])
    con.execute(
        "UPDATE images SET mtime=?, size=?, model=?, parse_ok=? WHERE id=?",
        (mtime, size, model, 1 if parse_ok else 0, image_id),
    )
    # Clear out previous tag/lora/category rows so we can rewrite cleanly
    con.execute("DELETE FROM image_tags WHERE image_id=?", (image_id,))
    con.execute("DELETE FROM image_loras WHERE image_id=?", (image_id,))
    con.execute("DELETE FROM image_categories WHERE image_id=?", (image_id,))
    return image_id


def delete_image(con: sqlite3.Connection, image_id: int) -> None:
    con.execute("DELETE FROM images WHERE id=?", (image_id,))


# ---------- tags / loras ----------

def get_or_create_tag(con: sqlite3.Connection, normalized: str) -> int:
    row = con.execute("SELECT id FROM tags WHERE normalized=?", (normalized,)).fetchone()
    if row:
        return int(row["id"])
    cur = con.execute("INSERT INTO tags(normalized) VALUES (?)", (normalized,))
    return int(cur.lastrowid)


def attach_tags(
    con: sqlite3.Connection,
    image_id: int,
    normalized_tags: Iterable[str],
    kind: int,
) -> None:
    pairs = []
    for t in normalized_tags:
        if not t:
            continue
        tag_id = get_or_create_tag(con, t)
        pairs.append((image_id, tag_id, kind))
    if pairs:
        con.executemany(
            "INSERT OR IGNORE INTO image_tags(image_id, tag_id, kind) VALUES (?,?,?)",
            pairs,
        )


def get_or_create_lora(con: sqlite3.Connection, name: str) -> int:
    row = con.execute("SELECT id FROM loras WHERE name=?", (name,)).fetchone()
    if row:
        return int(row["id"])
    cur = con.execute("INSERT INTO loras(name) VALUES (?)", (name,))
    return int(cur.lastrowid)


def attach_loras(
    con: sqlite3.Connection,
    image_id: int,
    lora_names: Iterable[str],
) -> None:
    pairs = []
    for n in lora_names:
        if not n:
            continue
        lora_id = get_or_create_lora(con, n)
        pairs.append((image_id, lora_id))
    if pairs:
        con.executemany(
            "INSERT OR IGNORE INTO image_loras(image_id, lora_id) VALUES (?,?)",
            pairs,
        )


# ---------- category materialization ----------

def rematerialize_categories_for_image(
    con: sqlite3.Connection,
    image_id: int,
) -> None:
    """Recompute image_categories rows for a single image based on its tags,
    LoRAs, and the current tag_categories / lora_categories mappings."""
    con.execute("DELETE FROM image_categories WHERE image_id=?", (image_id,))
    con.execute(
        """
        INSERT OR IGNORE INTO image_categories(image_id, category_id)
        SELECT image_id, category_id FROM (
            SELECT it.image_id AS image_id, tc.category_id AS category_id
            FROM image_tags AS it
            JOIN tag_categories AS tc ON tc.tag_id = it.tag_id
            WHERE it.image_id = ?
            UNION
            SELECT il.image_id AS image_id, lc.category_id AS category_id
            FROM image_loras AS il
            JOIN lora_categories AS lc ON lc.lora_id = il.lora_id
            WHERE il.image_id = ?
        )
        """,
        (image_id, image_id),
    )


def rematerialize_categories_for_category(
    con: sqlite3.Connection,
    category_id: int,
) -> None:
    """Recompute image_categories rows for the given category."""
    con.execute(
        "DELETE FROM image_categories WHERE category_id=?",
        (category_id,),
    )
    con.execute(
        """
        INSERT OR IGNORE INTO image_categories(image_id, category_id)
        SELECT image_id, ? FROM (
            SELECT it.image_id AS image_id
            FROM tag_categories AS tc
            JOIN image_tags AS it ON it.tag_id = tc.tag_id
            WHERE tc.category_id = ?
            UNION
            SELECT il.image_id AS image_id
            FROM lora_categories AS lc
            JOIN image_loras AS il ON il.lora_id = lc.lora_id
            WHERE lc.category_id = ?
        )
        """,
        (category_id, category_id, category_id),
    )


def rematerialize_all_categories(con: sqlite3.Connection) -> None:
    con.execute("DELETE FROM image_categories")
    con.execute(
        """
        INSERT OR IGNORE INTO image_categories(image_id, category_id)
        SELECT image_id, category_id FROM (
            SELECT it.image_id AS image_id, tc.category_id AS category_id
            FROM image_tags AS it
            JOIN tag_categories AS tc ON tc.tag_id = it.tag_id
            UNION
            SELECT il.image_id AS image_id, lc.category_id AS category_id
            FROM image_loras AS il
            JOIN lora_categories AS lc ON lc.lora_id = il.lora_id
        )
        """
    )


# ---------- meta key/value ----------

def meta_get(con: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO meta(key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )

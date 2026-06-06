"""Recursively walk the outputs folder, indexing new/changed PNGs."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional

from PIL import Image

from . import db, parser, paths


@dataclass
class ScanProgress:
    started_at: float = 0.0
    finished_at: float = 0.0
    running: bool = False
    counting: bool = False       # in the initial "count total files" phase
    total_files: int = 0         # total PNGs discovered up-front (0 while still counting)
    total_seen: int = 0          # PNGs visited on disk
    inserted: int = 0            # new images added
    updated: int = 0             # changed images re-indexed
    skipped_unchanged: int = 0   # files already in DB, identical mtime+size
    parse_failed: int = 0        # files with no readable metadata
    errors: List[str] = field(default_factory=list)
    current_path: Optional[str] = None

    def snapshot(self) -> dict:
        return {
            "running": self.running,
            "counting": self.counting,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_files": self.total_files,
            "total_seen": self.total_seen,
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped_unchanged": self.skipped_unchanged,
            "parse_failed": self.parse_failed,
            "errors": list(self.errors[-10:]),
            "current_path": self.current_path,
        }


_progress = ScanProgress()
_progress_lock = threading.Lock()
_scan_thread: Optional[threading.Thread] = None
_THUMB_MAX = 256


def get_progress() -> dict:
    with _progress_lock:
        return _progress.snapshot()


def _set_progress(**kwargs) -> None:
    with _progress_lock:
        for k, v in kwargs.items():
            setattr(_progress, k, v)


def _walk_pngs(roots: Iterable[str]) -> Iterable[str]:
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if fn.lower().endswith(".png"):
                    yield os.path.join(dirpath, fn)


def _ensure_thumb(image_path: str, image_id: int) -> None:
    thumb = paths.thumb_path_for(image_id)
    if os.path.isfile(thumb):
        return
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            img.thumbnail((_THUMB_MAX, _THUMB_MAX), Image.LANCZOS)
            img.save(thumb, "JPEG", quality=82, optimize=True)
    except Exception:
        # Non-fatal: gallery will just show nothing for this file
        pass


def _index_file(path: str, *, full_rescan: bool) -> str:
    """Index a single PNG. Returns one of: 'inserted','updated','skipped','failed'."""
    st = parser.file_stat(path)
    if st is None:
        return "failed"
    size = st.st_size
    mtime = st.st_mtime

    with db.connect() as con:
        existing = db.get_image_by_path(con, path)
        if existing and not full_rescan:
            if int(existing["size"]) == size and abs(float(existing["mtime"]) - mtime) < 1e-6:
                # Ensure a thumbnail exists even for previously-indexed images
                _ensure_thumb(path, int(existing["id"]))
                return "skipped"

        parsed = parser.parse_file(path)

        image_id = db.upsert_image(
            con,
            path=path,
            mtime=mtime,
            size=size,
            model=parsed.model,
            parse_ok=parsed.parse_ok,
        )
        if parsed.positive:
            db.attach_tags(con, image_id, parsed.positive, db.KIND_POS)
        if parsed.negative:
            db.attach_tags(con, image_id, parsed.negative, db.KIND_NEG)
        if parsed.loras:
            db.attach_loras(con, image_id, parsed.loras)
        db.rematerialize_categories_for_image(con, image_id)

    _ensure_thumb(path, image_id)

    if existing is None:
        return "inserted"
    return "updated" if parsed.parse_ok or not existing["parse_ok"] else "updated"


def _scan_worker(*, full_rescan: bool, on_done: Optional[Callable[[], None]] = None) -> None:
    db.init_db()
    _set_progress(
        running=True,
        counting=True,
        started_at=time.time(),
        finished_at=0.0,
        total_files=0,
        total_seen=0,
        inserted=0,
        updated=0,
        skipped_unchanged=0,
        parse_failed=0,
        errors=[],
        current_path=None,
    )

    roots = paths.outputs_roots()
    try:
        # Phase 1: count files so the UI can show a determinate progress bar.
        # We materialize the list so the second pass cannot disagree with the count
        # if files are added/removed mid-scan.
        all_paths = list(_walk_pngs(roots))
        _set_progress(counting=False, total_files=len(all_paths))

        # Phase 2: index files.
        for path in all_paths:
            try:
                _set_progress(current_path=path, total_seen=_progress.total_seen + 1)
                result = _index_file(path, full_rescan=full_rescan)
                with _progress_lock:
                    if result == "inserted":
                        _progress.inserted += 1
                    elif result == "updated":
                        _progress.updated += 1
                    elif result == "skipped":
                        _progress.skipped_unchanged += 1
                    elif result == "failed":
                        _progress.parse_failed += 1
            except Exception as e:  # noqa: BLE001
                with _progress_lock:
                    _progress.errors.append(f"{path}: {e!r}")
    finally:
        _set_progress(running=False, counting=False, current_path=None, finished_at=time.time())
        if on_done is not None:
            try:
                on_done()
            except Exception:
                pass


def start_scan(*, full_rescan: bool = False) -> bool:
    """Start a background scan. Returns False if one is already running."""
    global _scan_thread
    with _progress_lock:
        if _scan_thread is not None and _scan_thread.is_alive():
            return False
        _scan_thread = threading.Thread(
            target=_scan_worker,
            kwargs={"full_rescan": full_rescan},
            name="metadata-statistics-scan",
            daemon=True,
        )
        _scan_thread.start()
    return True


def is_running() -> bool:
    with _progress_lock:
        return _scan_thread is not None and _scan_thread.is_alive()

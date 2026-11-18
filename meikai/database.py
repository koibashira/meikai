"""SQLite WAL database for tab persistence, soft-delete, and cache management."""
import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from .config import DB_PATH, CACHE_DIR, DEFAULT_CACHE_BUDGET_MB


def _connect() -> sqlite3.Connection:
    os.makedirs(CACHE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tabs (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL DEFAULT 'Untitled',
                latex       TEXT NOT NULL DEFAULT '',
                tags        TEXT NOT NULL DEFAULT '[]',
                orig_path   TEXT,
                warp_path   TEXT,
                crop_points TEXT,
                pinned      INTEGER NOT NULL DEFAULT 0,
                deleted     INTEGER NOT NULL DEFAULT 0,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL,
                sort_order  INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS deleted_stack (
                id          TEXT PRIMARY KEY,
                tab_id      TEXT NOT NULL,
                deleted_at  REAL NOT NULL
            );
        """)


class Database:
    def __init__(self):
        init_db()
        self._lock = threading.RLock()
        self._conn = _connect()

    def _exec(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def _commit(self):
        with self._lock:
            self._conn.commit()

    # ── Tab CRUD ──────────────────────────────────────────────────────────────
    def create_tab(self, title: str = "Untitled") -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            tid = str(uuid.uuid4())
            max_order = self._exec("SELECT MAX(sort_order) FROM tabs WHERE deleted=0").fetchone()[0] or 0
            self._exec(
                "INSERT INTO tabs (id,title,latex,tags,pinned,deleted,created_at,updated_at,sort_order) "
                "VALUES (?,?,?,?,0,0,?,?,?)",
                (tid, title, "", "[]", now, now, max_order + 1),
            )
            self._commit()
            return self.get_tab(tid)

    def get_tab(self, tid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._exec("SELECT * FROM tabs WHERE id=?", (tid,)).fetchone()
            return self._row_to_dict(row) if row else None

    def get_all_tabs(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._exec(
                "SELECT * FROM tabs WHERE deleted=0 ORDER BY sort_order ASC, created_at ASC"
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def update_tab(self, tid: str, **kwargs) -> None:
        with self._lock:
            allowed = {"title", "latex", "tags", "orig_path", "warp_path", "crop_points", "pinned", "sort_order"}
            updates = {k: v for k, v in kwargs.items() if k in allowed}
            if not updates:
                return
            updates["updated_at"] = time.time()
            cols = ", ".join(f"{k}=?" for k in updates)
            self._exec(f"UPDATE tabs SET {cols} WHERE id=?", (*updates.values(), tid))
            self._commit()

    def soft_delete_tab(self, tid: str) -> None:
        """Move to deleted_stack (LIFO restore possible)."""
        with self._lock:
            now = time.time()
            self._exec("UPDATE tabs SET deleted=1, updated_at=? WHERE id=?", (now, tid))
            self._exec(
                "INSERT OR REPLACE INTO deleted_stack (id,tab_id,deleted_at) VALUES (?,?,?)",
                (str(uuid.uuid4()), tid, now),
            )
            self._commit()

    def restore_last_deleted(self) -> Optional[str]:
        with self._lock:
            while True:
                row = self._exec(
                    "SELECT tab_id FROM deleted_stack ORDER BY deleted_at DESC LIMIT 1"
                ).fetchone()
                if not row:
                    return None
                tid = row[0]
                self._exec("DELETE FROM deleted_stack WHERE tab_id=?", (tid,))
                # Verify tab still exists in database (was not permanently purged by cache prune)
                tab_exists = self._exec("SELECT id FROM tabs WHERE id=?", (tid,)).fetchone()
                if tab_exists:
                    self._exec("UPDATE tabs SET deleted=0, updated_at=? WHERE id=?", (time.time(), tid))
                    self._commit()
                    return tid
                self._commit()

    def search_tabs(self, query: str) -> List[Dict[str, Any]]:
        with self._lock:
            q = f"%{query.lower()}%"
            rows = self._exec(
                "SELECT * FROM tabs WHERE deleted=0 AND (LOWER(title) LIKE ? OR LOWER(latex) LIKE ? OR LOWER(tags) LIKE ?) "
                "ORDER BY updated_at DESC",
                (q, q, q),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    # ── Cache budget ──────────────────────────────────────────────────────────
    def prune_cache(self, budget_mb: int = DEFAULT_CACHE_BUDGET_MB) -> int:
        with self._lock:
            budget_bytes = budget_mb * 1024 * 1024
            # Measure cache size
            total = sum(
                os.path.getsize(os.path.join(CACHE_DIR, f))
                for f in os.listdir(CACHE_DIR)
                if os.path.isfile(os.path.join(CACHE_DIR, f))
            )
            if total <= budget_bytes:
                return 0
            # Purge soft-deleted first
            deleted = self._exec(
                "SELECT id, orig_path, warp_path FROM tabs WHERE deleted=1 ORDER BY updated_at ASC"
            ).fetchall()
            pruned = 0
            for row in deleted:
                for path in (row[1], row[2]):
                    if path and os.path.isfile(path):
                        try:
                            os.remove(path)
                            pruned += 1
                        except Exception:
                            pass
                self._exec("DELETE FROM tabs WHERE id=?", (row[0],))
                self._exec("DELETE FROM deleted_stack WHERE tab_id=?", (row[0],))
            self._commit()
            return pruned

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        cols = ["id", "title", "latex", "tags", "orig_path", "warp_path",
                "crop_points", "pinned", "deleted", "created_at", "updated_at", "sort_order"]
        d = dict(zip(cols, row))
        try:
            d["tags"] = json.loads(d["tags"]) if d["tags"] else []
        except Exception:
            d["tags"] = []
        try:
            d["crop_points"] = json.loads(d["crop_points"]) if d["crop_points"] else None
        except Exception:
            d["crop_points"] = None
        d["pinned"] = bool(d["pinned"])
        d["deleted"] = bool(d["deleted"])
        return d


# Singleton
_db: Optional[Database] = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db

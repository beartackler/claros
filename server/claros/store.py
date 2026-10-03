"""SQLite persistence: append-only session log, kv tables, FTS5 + sqlite-vec search.

    from claros.store import store
    store.log(session_id, kind, payload, t)        ; store.iter_log(session_id, kinds=None)
    store.kv_put(ns, id, obj) / kv_get / kv_list / kv_delete     (ns: sessions, requests, keyframes, mastery, ...)
    store.put_workflow(workmap) / get_workflow(wid, version=None) / list_workflows()
    store.put_keyframe(id, session_id, path, t, meta) / get_keyframe(id)
    store.index_text(ns, id, text) / search_text(ns, q, k=10) -> [{id, text, score}]
    store.index_vec(ns, id, vec)  / search_vec(ns, vec, k=10) -> [{id, distance}]   (cosine)

DB path: env CLAROS_DB (default ./data/claros.db, relative to repo root). ":memory:" works.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Optional

import numpy as np

from . import REPO_ROOT

log = logging.getLogger("claros.store")

try:
    import sqlite_vec
except Exception:  # noqa: BLE001
    sqlite_vec = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  t REAL,
  ts REAL NOT NULL,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS log_session ON log(session_id, id);
CREATE TABLE IF NOT EXISTS kv (
  ns TEXT NOT NULL, id TEXT NOT NULL, value TEXT NOT NULL, updated REAL NOT NULL,
  PRIMARY KEY (ns, id)
);
CREATE TABLE IF NOT EXISTS workflows (
  workflow_id TEXT NOT NULL, version INTEGER NOT NULL, value TEXT NOT NULL, created REAL NOT NULL,
  PRIMARY KEY (workflow_id, version)
);
CREATE TABLE IF NOT EXISTS keyframes (
  id TEXT PRIMARY KEY, session_id TEXT, path TEXT NOT NULL, t REAL, meta TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(ns UNINDEXED, doc_id UNINDEXED, text);
CREATE TABLE IF NOT EXISTS vecs (
  ns TEXT NOT NULL, id TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL,
  PRIMARY KEY (ns, id)
);
"""


def _resolve_path(p: Optional[str]) -> str:
    p = p or os.getenv("CLAROS_DB") or "./data/claros.db"
    if p == ":memory:":
        return p
    path = Path(p)
    if not path.is_absolute():
        path = REPO_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


class Store:
    def __init__(self, path: Optional[str] = None) -> None:
        self._lock = threading.RLock()
        self.path = _resolve_path(path)
        self.has_vec = False
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        if sqlite_vec is not None:
            try:
                self.conn.enable_load_extension(True)
                sqlite_vec.load(self.conn)
                self.conn.enable_load_extension(False)
                self.has_vec = True
            except Exception as e:  # noqa: BLE001
                log.warning("sqlite-vec unavailable (%s); using numpy cosine fallback", e)
        self.conn.executescript(SCHEMA)

    # ---- low level ----
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ---- append-only log ----
    def log(self, session_id: str, kind: str, payload: Any, t: Optional[float] = None) -> int:
        cur = self.execute(
            "INSERT INTO log(session_id, kind, t, ts, payload) VALUES (?,?,?,?,?)",
            (session_id, kind, t, time.time(), json.dumps(payload, default=_default)),
        )
        return int(cur.lastrowid)

    def iter_log(self, session_id: str, kinds: Optional[list[str]] = None,
                 after_id: int = 0) -> Iterator[dict]:
        sql = "SELECT * FROM log WHERE session_id=? AND id>?"
        params: list[Any] = [session_id, after_id]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params += kinds
        sql += " ORDER BY id"
        rows = self.execute(sql, tuple(params)).fetchall()
        for r in rows:
            yield {"id": r["id"], "session_id": r["session_id"], "kind": r["kind"], "t": r["t"],
                   "ts": r["ts"], "payload": json.loads(r["payload"])}

    # ---- kv ----
    def kv_put(self, ns: str, id: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(ns,id,value,updated) VALUES (?,?,?,?) "
            "ON CONFLICT(ns,id) DO UPDATE SET value=excluded.value, updated=excluded.updated",
            (ns, id, json.dumps(value, default=_default), time.time()),
        )

    def kv_get(self, ns: str, id: str) -> Optional[Any]:
        r = self.execute("SELECT value FROM kv WHERE ns=? AND id=?", (ns, id)).fetchone()
        return json.loads(r["value"]) if r else None

    def kv_list(self, ns: str, prefix: str = "") -> list[Any]:
        rows = self.execute(
            "SELECT value FROM kv WHERE ns=? AND id LIKE ? ORDER BY updated", (ns, prefix + "%")
        ).fetchall()
        return [json.loads(r["value"]) for r in rows]

    def kv_delete(self, ns: str, id: str) -> None:
        self.execute("DELETE FROM kv WHERE ns=? AND id=?", (ns, id))

    # ---- workflows (WorkMap by version) ----
    def put_workflow(self, workmap: Any, version: Optional[int] = None) -> int:
        d = workmap.model_dump(mode="json") if hasattr(workmap, "model_dump") else dict(workmap)
        wid = d["workflow_id"]
        if version is None:
            r = self.execute("SELECT MAX(version) v FROM workflows WHERE workflow_id=?", (wid,)).fetchone()
            version = max(int(d.get("version") or 1), (r["v"] or 0) + 1)
        d["version"] = version
        self.execute(
            "INSERT OR REPLACE INTO workflows(workflow_id,version,value,created) VALUES (?,?,?,?)",
            (wid, version, json.dumps(d, default=_default), time.time() * 1000.0),
        )
        return version

    @staticmethod
    def _wf_row(r: sqlite3.Row) -> dict:
        d = json.loads(r["value"])
        c = float(r["created"])
        d["updated_at"] = c * 1000.0 if c < 1e11 else c  # epoch ms (legacy rows stored seconds)
        return d

    def get_workflow(self, workflow_id: str, version: Optional[int] = None) -> Optional[dict]:
        """WorkMap JSON + `updated_at` (epoch ms of that version)."""
        if version is None:
            r = self.execute("SELECT value, created FROM workflows WHERE workflow_id=? ORDER BY version DESC LIMIT 1",
                             (workflow_id,)).fetchone()
        else:
            r = self.execute("SELECT value, created FROM workflows WHERE workflow_id=? AND version=?",
                             (workflow_id, version)).fetchone()
        return self._wf_row(r) if r else None

    def list_workflows(self) -> list[dict]:
        """Latest version per workflow, most recently updated first; each has `updated_at` (epoch ms)."""
        rows = self.execute(
            "SELECT w.value, w.created FROM workflows w JOIN (SELECT workflow_id, MAX(version) v FROM workflows "
            "GROUP BY workflow_id) m ON w.workflow_id=m.workflow_id AND w.version=m.v"
        ).fetchall()
        return sorted((self._wf_row(r) for r in rows), key=lambda d: -d["updated_at"])

    # ---- keyframes (redacted JPEG on disk) ----
    def put_keyframe(self, id: str, session_id: str, path: str, t: Optional[float] = None,
                     meta: Optional[dict] = None) -> None:
        self.execute("INSERT OR REPLACE INTO keyframes(id,session_id,path,t,meta) VALUES (?,?,?,?,?)",
                     (id, session_id, str(path), t, json.dumps(meta or {})))

    def get_keyframe(self, id: str) -> Optional[dict]:
        r = self.execute("SELECT * FROM keyframes WHERE id=?", (id,)).fetchone()
        if not r:
            return None
        return {"id": r["id"], "session_id": r["session_id"], "path": r["path"], "t": r["t"],
                "meta": json.loads(r["meta"] or "{}")}

    # ---- FTS5 ----
    def index_text(self, ns: str, id: str, text: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM fts WHERE ns=? AND doc_id=?", (ns, id))
            self.conn.execute("INSERT INTO fts(ns, doc_id, text) VALUES (?,?,?)", (ns, id, text))

    def search_text(self, ns: str, q: str, k: int = 10) -> list[dict]:
        toks = [t for t in re.findall(r"\w+", q.lower(), flags=re.UNICODE) if len(t) > 1]
        if not toks:
            return []
        match = " OR ".join(f'"{t}"' for t in toks)
        try:
            rows = self.execute(
                "SELECT doc_id, text, bm25(fts) AS score FROM fts WHERE fts MATCH ? AND ns=? "
                "ORDER BY score LIMIT ?", (match, ns, k)).fetchall()
        except sqlite3.OperationalError as e:
            log.warning("fts search failed: %s", e)
            return []
        return [{"id": r["doc_id"], "text": r["text"], "score": -float(r["score"])} for r in rows]

    # ---- vectors (cosine) ----
    def index_vec(self, ns: str, id: str, vec: Any) -> None:
        a = np.asarray(vec, dtype=np.float32).ravel()
        self.execute(
            "INSERT OR REPLACE INTO vecs(ns,id,dim,vec) VALUES (?,?,?,?)", (ns, id, int(a.size), a.tobytes()))

    def search_vec(self, ns: str, vec: Any, k: int = 10) -> list[dict]:
        a = np.asarray(vec, dtype=np.float32).ravel()
        if self.has_vec:
            rows = self.execute(
                "SELECT id, vec_distance_cosine(vec, ?) AS d FROM vecs WHERE ns=? AND dim=? "
                "ORDER BY d LIMIT ?", (a.tobytes(), ns, int(a.size), k)).fetchall()
            return [{"id": r["id"], "distance": float(r["d"])} for r in rows]
        rows = self.execute("SELECT id, vec FROM vecs WHERE ns=? AND dim=?", (ns, int(a.size))).fetchall()
        if not rows:
            return []
        m = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
        sims = m @ a / (np.linalg.norm(m, axis=1) * (np.linalg.norm(a) or 1.0) + 1e-9)
        order = np.argsort(-sims)[:k]
        return [{"id": rows[i]["id"], "distance": float(1 - sims[i])} for i in order]


def _default(o: Any) -> Any:
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    if isinstance(o, (np.ndarray, np.generic)):
        return o.tolist()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


class _StoreProxy:
    """Lazily opens the default Store; `store.open(path)` re-points it (tests)."""

    def __init__(self) -> None:
        self._s: Optional[Store] = None

    def open(self, path: Optional[str] = None) -> Store:
        if self._s is not None:
            try:
                self._s.close()
            except Exception:  # noqa: BLE001
                pass
        self._s = Store(path)
        return self._s

    def __getattr__(self, name: str) -> Any:
        if self._s is None:
            self._s = Store()
        return getattr(self._s, name)


store: Any = _StoreProxy()

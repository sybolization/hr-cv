"""会话持久化：SQLite 单库（{data_dir}/app.db），事务性写入。

公开接口与旧 JSON 版完全一致（main.py 无需感知实现）：
    new_session(sid) / save(session) / delete(sid) / load_all()

内存结构：
    session = {id, title, created_at, updated_at,
               resumes: {rid: {id, filename, format, data(bytes), parsed}},
               messages: [{role, content, refs?, reasoning?, reasoning_seconds?}]}
库表：sessions / resumes（原始字节 BLOB + 解析缓存）/ messages。

异常策略：DB 异常先 logger.exception 记录完整堆栈，再原样抛出（不吞、不改写）。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from hr_cv.config import settings

logger = logging.getLogger(__name__)

_DB_PATH = settings.data_dir / "app.db"
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '新会话',
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS resumes(
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  filename TEXT,
  format TEXT,
  data BLOB NOT NULL,
  method TEXT,
  content TEXT,
  pages_json TEXT,
  failed TEXT
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  role TEXT NOT NULL,
  content TEXT,
  refs_json TEXT,
  reasoning TEXT,
  reasoning_seconds REAL,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    """惰性建连（含建目录/建表）。共享连接 + 模块级锁串行化，demo 规模足够。"""
    global _conn
    if _conn is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(_SCHEMA)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.commit()
        logger.info("SQLite 就绪: %s", _DB_PATH)
    return _conn


def new_session(sid: str) -> dict:
    """构造一个空会话的内存结构。"""
    return {
        "id": sid,
        "title": "新会话",
        "created_at": time.time(),
        "updated_at": time.time(),
        "resumes": {},
        "messages": [],
    }


def save(session: dict) -> None:
    """整会话保存：upsert 会话行 + 重写该会话的 resumes/messages（单事务）。"""
    sid = session["id"]
    with _lock:
        conn = _connect()
        try:
            session["updated_at"] = time.time()
            conn.execute(
                "INSERT INTO sessions(id, title, created_at, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at",
                (sid, session["title"], session["created_at"], session["updated_at"]),
            )
            conn.execute("DELETE FROM resumes WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM messages WHERE session_id=?", (sid,))
            for r in session["resumes"].values():
                parsed = r.get("parsed") or {}
                conn.execute(
                    "INSERT INTO resumes(id, session_id, filename, format, data, "
                    "method, content, pages_json, failed) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        r["id"], sid, r["filename"], r["format"], r["data"],
                        parsed.get("method"), parsed.get("content"),
                        json.dumps(parsed.get("pages") or [], ensure_ascii=False),
                        parsed.get("failed"),
                    ),
                )
            for seq, m in enumerate(session["messages"]):
                conn.execute(
                    "INSERT INTO messages(session_id, seq, role, content, refs_json, "
                    "reasoning, reasoning_seconds, created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        sid, seq, m["role"], m.get("content"),
                        json.dumps(m.get("refs") or [], ensure_ascii=False),
                        m.get("reasoning"), m.get("reasoning_seconds"), time.time(),
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("SQLite save 失败 (session=%s)", sid)
            raise


def delete(sid: str) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("SQLite delete 失败 (session=%s)", sid)
            raise


def set_setting(key: str, value: str) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("set_setting 失败 (key=%s)", key)
            raise


def get_setting(key: str, default: str | None = None) -> str | None:
    with _lock:
        conn = _connect()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default


def get_llm_overrides() -> dict[str, str]:
    """页面保存的 LLM 配置（仅返回有值的键）。"""
    keys = ("llm_base_url", "llm_api_key", "llm_model", "llm_reasoning_effort")
    out = {}
    with _lock:
        conn = _connect()
        for k in keys:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
            if row and row[0]:
                out[k.removeprefix("llm_")] = row[0]
    return out


def load_all() -> dict[str, dict]:
    """启动时从 SQLite 重建全部内存会话。"""
    with _lock:
        conn = _connect()
        sessions: dict[str, dict] = {}
        for sid, title, created_at, updated_at in conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions"
        ):
            sessions[sid] = {
                "id": sid,
                "title": title,
                "created_at": created_at,
                "updated_at": updated_at,
                "resumes": {},
                "messages": [],
            }
        for rid, sid, filename, fmt, data, method, content, pages_json, failed in conn.execute(
            "SELECT id, session_id, filename, format, data, method, content, pages_json, failed "
            "FROM resumes"
        ):
            if failed:
                parsed: dict | None = {"failed": failed}
            elif content is not None:
                parsed = {
                    "content": content,
                    "method": method,
                    "pages": json.loads(pages_json) if pages_json else [],
                }
            else:
                parsed = None
            sessions[sid]["resumes"][rid] = {
                "id": rid,
                "filename": filename,
                "format": fmt,
                "data": data,
                "parsed": parsed,
            }
        for sid, role, content, refs_json, reasoning, reasoning_seconds in conn.execute(
            "SELECT session_id, role, content, refs_json, reasoning, reasoning_seconds "
            "FROM messages ORDER BY session_id, seq"
        ):
            sessions[sid]["messages"].append({
                "role": role,
                "content": content,
                "refs": json.loads(refs_json) if refs_json else [],
                "reasoning": reasoning,
                "reasoning_seconds": reasoning_seconds,
            })
        logger.info("SQLite load_all: %d 个会话", len(sessions))
        return sessions

# -*- coding: utf-8 -*-
"""工具状态库 state.db（五表），独立于微信库。

线程模型：每次操作独立连接（sqlite3 自带并发保护），适配多线程 worker。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from app.config import STATE_DB

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv_config(
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS monitor_group(
    chatroom_id      TEXT PRIMARY KEY,
    group_name       TEXT,
    enabled          INTEGER DEFAULT 1,
    sort_seq_cursor  INTEGER DEFAULT 0,
    monitor_start_date TEXT,
    last_summary_at  TEXT,
    last_docx_path   TEXT
);
CREATE TABLE IF NOT EXISTS summary_run(
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chatroom_id  TEXT,
    date         TEXT,
    seq_start    INTEGER,
    seq_end      INTEGER,
    msg_count    INTEGER,
    status       TEXT,       -- running/done/failed
    docx_path    TEXT,
    created_at   TEXT
);
CREATE TABLE IF NOT EXISTS archived_file(
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chatroom_id  TEXT,
    date         TEXT,
    orig_name    TEXT,
    saved_path   TEXT,
    size         INTEGER,
    svr_id       TEXT,
    archived_at  TEXT,
    status       TEXT
);
CREATE TABLE IF NOT EXISTS error_log(
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT,
    level   TEXT,
    module  TEXT,
    message TEXT
);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class StateStore:
    def __init__(self, db_path: Path = STATE_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._conn()
        conn.executescript(_SCHEMA)
        self._migrate(conn)
        conn.commit()
        conn.close()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """旧版 state.db 增量加列（SQLite 的 ALTER ADD COLUMN 幂等需自查）。"""
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(monitor_group)").fetchall()}
        if "monitor_start_date" not in cols:
            conn.execute(
                "ALTER TABLE monitor_group ADD COLUMN monitor_start_date TEXT")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ---------- kv ----------
    def get_kv(self, key: str, default: str | None = None) -> str | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT value FROM kv_config WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else default
        finally:
            conn.close()

    def set_kv(self, key: str, value: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO kv_config(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()

    # ---------- 监控群 ----------
    def list_groups(self, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM monitor_group"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY group_name"
        conn = self._conn()
        try:
            return [dict(r) for r in conn.execute(sql).fetchall()]
        finally:
            conn.close()

    def get_group(self, chatroom_id: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM monitor_group WHERE chatroom_id=?", (chatroom_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def upsert_group(self, chatroom_id: str, group_name: str,
                     enabled: bool = True,
                     monitor_start_date: str | None = None) -> None:
        """新增或更名，不覆盖已有游标/启用日期。

        新插入群游标置 -1（待后台初始化为当天起点的标记）；
        monitor_start_date 仅在新插入时写入。
        """
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO monitor_group(chatroom_id, group_name, enabled, "
                "monitor_start_date, sort_seq_cursor) VALUES(?,?,?,?, -1) "
                "ON CONFLICT(chatroom_id) DO UPDATE SET group_name=excluded.group_name",
                (chatroom_id, group_name, int(enabled), monitor_start_date),
            )
            conn.commit()
        finally:
            conn.close()

    def remove_group(self, chatroom_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "DELETE FROM monitor_group WHERE chatroom_id=?", (chatroom_id,)
            )
            conn.commit()
        finally:
            conn.close()

    def set_group_enabled(self, chatroom_id: str, enabled: bool) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE monitor_group SET enabled=? WHERE chatroom_id=?",
                (int(enabled), chatroom_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_cursor(self, chatroom_id: str) -> int:
        g = self.get_group(chatroom_id)
        return int(g["sort_seq_cursor"] or 0) if g else 0

    def advance_cursor(self, chatroom_id: str, new_seq: int) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE monitor_group SET sort_seq_cursor=MAX(sort_seq_cursor,?) "
                "WHERE chatroom_id=?",
                (int(new_seq), chatroom_id),
            )
            conn.commit()
        finally:
            conn.close()

    def set_cursor(self, chatroom_id: str, new_seq: int) -> None:
        """强制设置游标（允许倒退，供手动重置/重新总结场景）。"""
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE monitor_group SET sort_seq_cursor=? WHERE chatroom_id=?",
                (int(new_seq), chatroom_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_summaried(self, chatroom_id: str, docx_path: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE monitor_group SET last_summary_at=?, last_docx_path=? "
                "WHERE chatroom_id=?",
                (_now(), docx_path, chatroom_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ---------- summary_run ----------
    def start_summary_run(self, chatroom_id: str, date: str,
                          seq_start: int) -> int:
        conn = self._conn()
        try:
            cur = conn.execute(
                "INSERT INTO summary_run(chatroom_id,date,seq_start,status,created_at) "
                "VALUES(?,?,?,?,?)",
                (chatroom_id, date, seq_start, "running", _now()),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def finish_summary_run(self, run_id: int, seq_end: int, msg_count: int,
                           status: str, docx_path: str = "") -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE summary_run SET seq_end=?, msg_count=?, status=?, docx_path=? "
                "WHERE id=?",
                (seq_end, msg_count, status, docx_path, run_id),
            )
            conn.commit()
        finally:
            conn.close()

    def last_finished_run(self, chatroom_id: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM summary_run WHERE chatroom_id=? AND status='done' "
                "ORDER BY id DESC LIMIT 1",
                (chatroom_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ---------- archived_file ----------
    def upsert_archived_file(self, chatroom_id: str, date: str,
                             orig_name: str, saved_path: str, size: int,
                             svr_id: str, status: str = "archived") -> int:
        """登记/更新一条归档记录（幂等）。

        冲突识别：svr_id 非空时按 (chatroom_id, svr_id)；否则按
        (chatroom_id, date, orig_name, size)。pending/missing 重试
        不会再堆积重复行；同名不同大小视为不同文件各占一行。
        """
        conn = self._conn()
        try:
            row = None
            if svr_id:
                row = conn.execute(
                    "SELECT id FROM archived_file WHERE chatroom_id=? "
                    "AND svr_id=?", (chatroom_id, svr_id)).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT id FROM archived_file WHERE chatroom_id=? AND date=? "
                    "AND orig_name=? AND size=?",
                    (chatroom_id, date, orig_name, size)).fetchone()
            if row is None:
                cur = conn.execute(
                    "INSERT INTO archived_file(chatroom_id,date,orig_name,"
                    "saved_path,size,svr_id,archived_at,status) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (chatroom_id, date, orig_name, saved_path, size,
                     svr_id, _now(), status))
                rid = int(cur.lastrowid)
            else:
                rid = int(row["id"])
                conn.execute(
                    "UPDATE archived_file SET saved_path=?, size=?, status=?, "
                    "archived_at=? WHERE id=?",
                    (saved_path, size, status, _now(), rid))
            conn.commit()
            return rid
        finally:
            conn.close()

    def list_archived_files(self, chatroom_id: str | None = None,
                            date: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM archived_file WHERE 1=1", []
        if chatroom_id:
            sql += " AND chatroom_id=?"
            args.append(chatroom_id)
        if date:
            sql += " AND date=?"
            args.append(date)
        sql += " ORDER BY id"
        conn = self._conn()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    # ---------- error_log ----------
    def log_error(self, level: str, module: str, message: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO error_log(ts,level,module,message) VALUES(?,?,?,?)",
                (_now(), level, module, message[:2000]),
            )
            conn.commit()
        finally:
            conn.close()

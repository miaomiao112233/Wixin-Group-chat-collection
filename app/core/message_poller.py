# -*- coding: utf-8 -*-
"""增量消息拉取：封装 wechatauto.get_new_messages，解析昵称并清洗 XML。

微信 4.x 群消息发送者解析优先级（实测结论，勿改动顺序）：
  1. 内容前缀 "wxid_xxx:\\n正文"（非自己发的消息几乎都有）→ 最权威；
  2. XML 属性 fromusername（动画表情等卡片消息）；
  3. 库的 sender_username（来自全局 SenderName2Id，仅对老成员有效，
     失败时退化成纯数字串，需剔除）；
  4. 分片库本地 Name2Id 表 rowid → user_name（real_sender_id 的真实含义）。
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from html import unescape

from wechatauto import WeChatDB

from app.config import POLL_BATCH_LIMIT
from app.models import Message

log = logging.getLogger("app.db")

# 解密缓存页损坏（微信正在写库/WAL 撕裂）时的应用级退避：
# wechatauto 内部已做"清缓存重建+重试一次"，这里再给两轮等待，
# 让微信 checkpoint 完成、缓存重建稳定后自愈。
_MALFORMED_RETRY_DELAYS = (2.0, 4.0)


def _is_malformed(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.DatabaseError) \
        and "malformed" in str(exc).lower()


def db_call_with_retry(fn, what: str = "读消息"):
    """对微信解密库的只读调用做 malformed 退避重试。"""
    try:
        return fn()
    except sqlite3.DatabaseError as exc:
        if not _is_malformed(exc):
            raise
        for delay in _MALFORMED_RETRY_DELAYS:
            log.warning("%s时解密缓存损坏，%.0f 秒后重建重试: %s",
                        what, delay, exc)
            time.sleep(delay)
            try:
                return fn()
            except sqlite3.DatabaseError as exc2:
                if not _is_malformed(exc2):
                    raise
        raise

try:
    from lxml import etree

    _PARSER = etree.XMLParser(recover=True, resolve_entities=False,
                              no_network=True, huge_tree=False)
except Exception:  # pragma: no cover - lxml 缺失时退化为正则清洗
    etree = None

_TAG_RE = re.compile(r"<[^>]+>")
# 消息正文前的发送者前缀：'wxid_xxx:\n内容'（不含协议 XML 时长度有限）
_PREFIX_RE = re.compile(r"^([^:\n]{1,64}):\r?\n")


def _strip_tags(s: str, limit: int = 200) -> str:
    s = unescape(_TAG_RE.sub("", s or "")).strip()
    return re.sub(r"\s+", " ", s)[:limit]


class MessagePoller:
    """对单账号微信库做增量轮询；发送者/昵称解析带缓存。"""

    def __init__(self, db: WeChatDB):
        self._db = db
        self._nick_cache: dict[str, str] = {}
        self._member_map: dict[str, dict[str, str]] = {}  # chatroom -> wxid->昵称
        self._self_name_cache: str = ""
        self._self_wxid: str = db.wxid
        self._name2id: dict[int, str] | None = None

    # ---------- 对外 ----------
    def fetch_new(self, chatroom_id: str, since_seq: int,
                  limit: int = POLL_BATCH_LIMIT) -> list[Message]:
        def _once():
            rows = self._db.get_new_messages(
                chatroom_id, since_seq=since_seq, limit=limit)
            return [self._to_message(chatroom_id, r) for r in rows]
        return db_call_with_retry(_once, what=f"采集 {chatroom_id}")

    def latest_seq(self, chatroom_id: str) -> int:
        msgs = db_call_with_retry(
            lambda: self._db.get_messages(chatroom_id, limit=1),
            what=f"定位游标 {chatroom_id}")
        return int(msgs[0]["sort_seq"]) if msgs else 0

    def day_start_seq(self, chatroom_id: str, day=None,
                      scan_limit: int = 2000) -> int:
        """监控启用时的初始游标：当天最早消息的 sort_seq-1。

        - 当天已有消息 → 最早一条的前一位置（首轮即可拉到当天全部消息，
          但不会回溯启用日之前的历史）；
        - 当天尚无消息 → 当前最新 seq（此后只收真正的新消息）。
        """
        from datetime import date as _date
        day = day or _date.today()
        rows = db_call_with_retry(
            lambda: self._db.get_messages(chatroom_id, limit=scan_limit),
            what=f"定位当天起点 {chatroom_id}")  # 降序
        if not rows:
            return 0
        today_seqs = [
            int(r["sort_seq"]) for r in rows
            if datetime.fromtimestamp(int(r["create_time"])).date() == day
        ]
        if today_seqs:
            return max(0, min(today_seqs) - 1)
        return int(rows[0]["sort_seq"])

    def count_today(self, chatroom_id: str,
                    scan_limit: int = 10000) -> int:
        """今天 0:00 至今的消息总数（只计数不清洗，跨午夜自动正确）。

        用于卡片"今日累计 N 条"显示，区别于"本轮新增"。
        大群一天消息可能超 scan_limit，此时数字偏低（按降序取最近 N 条过滤）。
        """
        from datetime import date as _date
        today = _date.today()
        rows = db_call_with_retry(
            lambda: self._db.get_messages(chatroom_id, limit=scan_limit),
            what=f"统计今日消息 {chatroom_id}")
        if not rows:
            return 0
        return sum(1 for r in rows
                   if datetime.fromtimestamp(
                       int(r["create_time"])).date() == today)

    def fetch_day(self, chatroom_id: str, day=None,
                  max_scan: int = 1000) -> list[Message]:
        """拉取某天（默认今天）的全量消息，升序返回，供全量重建总结。"""
        from datetime import date as _date
        day = day or _date.today()

        def _once():
            rows = self._db.get_messages(chatroom_id, limit=max_scan)  # 降序
            sel = [r for r in rows
                   if datetime.fromtimestamp(
                       int(r["create_time"])).date() == day]
            sel.reverse()
            return [self._to_message(chatroom_id, r) for r in sel]

        return db_call_with_retry(_once, what=f"总结取数 {chatroom_id}")

    # ---------- 发送者解析 ----------
    def _chatroom_members(self, chatroom_id: str) -> dict[str, str]:
        m = self._member_map.get(chatroom_id)
        if m is None:
            m = {}
            try:
                for mem in self._db.get_group_members(chatroom_id):
                    name = mem.get("remark") or mem.get("nick_name") or ""
                    if mem.get("username"):
                        m[mem["username"]] = name or mem["username"]
            except Exception:
                pass
            self._member_map[chatroom_id] = m
        return m

    def _global_name2id(self) -> dict[int, str]:
        """合并各分片库 Name2Id；同一 rowid 映射冲突时弃用该 rowid。"""
        if self._name2id is not None:
            return self._name2id
        votes: dict[int, dict[str, int]] = {}
        for rel, path, _ in getattr(self._db, "_db_files", []):
            if not re.match(r"^message_\d+\.db$",
                            os.path.basename(path).lower()):
                continue
            try:
                conn = self._db._open(rel)
            except Exception:
                continue
            try:
                for rid, u in conn.execute(
                        "SELECT rowid, user_name FROM Name2Id"):
                    if u:
                        votes.setdefault(int(rid), {})
                        votes[int(rid)][u] = votes[int(rid)].get(u, 0) + 1
            except Exception:
                pass
            finally:
                conn.close()
        idx = {rid: next(iter(u)) for rid, u in votes.items() if len(u) == 1}
        self._name2id = idx
        return idx

    def _self_name(self) -> str:
        if not self._self_name_cache:
            try:
                info = self._db.get_self_info() or {}
                self._self_name_cache = info.get("nickname") or self._self_wxid
            except Exception:
                self._self_name_cache = "我"
        return self._self_name_cache

    def _display_name(self, chatroom_id: str, wxid: str) -> str:
        """wxid → 展示名（群成员表 > contact 昵称 > wxid）。"""
        members = self._chatroom_members(chatroom_id)
        if wxid in members:
            return members[wxid]
        if wxid in self._nick_cache:
            return self._nick_cache[wxid]
        name = ""
        try:
            name = self._db.get_nickname(wxid) or ""
        except Exception:
            pass
        name = name or wxid
        self._nick_cache[wxid] = name
        return name

    def resolve_sender(self, chatroom_id: str, r: dict,
                       prefix_wxid: str, from_wxid: str) -> tuple[str, str]:
        """返回 (wxid, 展示名)。"""
        sender_id = r.get("sender_id")
        lib_user = str(r.get("sender_username") or "")
        if lib_user.isdigit():          # 库映射失败的数字串，视为未解析
            lib_user = ""

        wxid = prefix_wxid or from_wxid or lib_user
        if not wxid and isinstance(sender_id, int):
            wxid = self._global_name2id().get(sender_id, "")

        if not wxid:
            return "", "系统" if sender_id in (0, None, "") else f"用户{sender_id}"
        if wxid == self._self_wxid:
            return wxid, self._self_name()
        return wxid, self._display_name(chatroom_id, wxid)

    # ---------- 清洗 ----------
    def _to_message(self, chatroom_id: str, r: dict) -> Message:
        mtype = str(r.get("type") or "")
        raw = r.get("content") or ""
        if not isinstance(raw, str):
            raw = str(raw)

        prefix_wxid, body = "", raw
        m = _PREFIX_RE.match(raw)
        if m and m.group(1) not in ("http", "https"):
            prefix_wxid, body = m.group(1), raw[m.end():]

        content, file_name, svr_id, from_wxid = self._clean(mtype, body)
        wxid, name = self.resolve_sender(chatroom_id, r, prefix_wxid, from_wxid)
        # 文件消息 XML 常无 newmsgid，用库行 local_id（同群内稳定唯一）
        # 兜底，保证归档登记的幂等键非空
        svr_id = svr_id or str(r.get("local_id") or "")
        return Message(
            chatroom_id=chatroom_id,
            sort_seq=int(r["sort_seq"]),
            local_id=int(r.get("local_id") or 0),
            msg_type=mtype,
            sender_wxid=wxid,
            sender_name=name,
            content=content,
            create_time=datetime.fromtimestamp(int(r.get("create_time") or 0)),
            raw_content=raw[:5000],
            file_name=file_name,
            svr_id=svr_id,
        )

    @staticmethod
    def _clean(mtype: str, raw: str) -> tuple[str, str, str, str]:
        """返回 (可读文本, 文件名, svrid, fromusername)。"""
        raw = raw.strip()
        if "<" not in raw:
            return raw, "", "", ""

        root = None
        if etree is not None:
            try:
                root = etree.fromstring(raw.encode("utf-8"), _PARSER)
            except Exception:
                root = None

        if root is None:
            return _strip_tags(raw) or f"[{mtype}]", "", "", ""

        def find(tag: str):
            el = root.find(tag)
            return el if el is not None else root.find(f".//{tag}")

        # 媒体占位
        for tag, label in (("img", "[图片]"), ("video", "[视频]"),
                           ("voipmsg", "[音视频通话]")):
            if find(tag) is not None:
                return label, "", "", ""
        emoji = find("emoji")
        if emoji is not None:
            return "[动画表情]", "", "", emoji.get("fromusername") or ""

        appmsg = root if root.tag == "appmsg" else find("appmsg")
        if appmsg is None:
            return _strip_tags(raw) or f"[{mtype}]", "", "", ""

        title = (appmsg.findtext("title") or "").strip()
        svr_id = (appmsg.findtext("newmsgid")
                  or appmsg.findtext("appmsgid") or "").strip()
        try:
            atype = int(appmsg.findtext("type") or 0)
        except ValueError:
            atype = 0

        if atype == 6:                                   # 文件
            return f"[文件] {title}", title, svr_id, ""
        if atype == 57:                                  # 引用
            refer = appmsg.find("refermsg")
            if refer is not None:
                rwho = _strip_tags(refer.findtext("displayname") or "", 40)
                rwhat = _strip_tags(refer.findtext("content") or "", 60)
                return f"「引用 {rwho}: {rwhat}」{title}", "", svr_id, ""
            return title or f"[{mtype}]", "", svr_id, ""
        if atype == 5:                                   # 链接
            return f"[链接] {title}", "", svr_id, ""
        if atype == 19:                                  # 合并转发
            return f"[合并转发] {title}", "", svr_id, ""
        if title:
            return f"[{mtype}] {title}", "", svr_id, ""
        return _strip_tags(raw) or f"[{mtype}]", "", svr_id, ""

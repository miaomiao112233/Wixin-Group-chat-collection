# -*- coding: utf-8 -*-
"""关键词告警的纯逻辑：词表解析、静默时段、命中判断、告警节流。

刻意不依赖 Qt / config，方便单测与在采集线程里直接调用。
"""
from __future__ import annotations

import re
from datetime import datetime, time

# 中英文逗号、分号、空白都当作分隔符
_SPLIT_RE = re.compile(r"[,，;；、\s]+")
_HHMM_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")
_SPAN_RE = re.compile(r"\s*[-~—－到至]\s*")


def parse_keywords(text: str) -> list[str]:
    """把"作业,考试；会议 通知"解析成去重词表（保序）。"""
    out: list[str] = []
    for part in _SPLIT_RE.split(text or ""):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def format_keywords(keywords: list[str]) -> str:
    """词表 -> 存储/输入框用的文本（逗号分隔）。"""
    return ", ".join(keywords or [])


def _parse_hhmm(text: str) -> time | None:
    m = _HHMM_RE.match(text or "")
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def parse_silence(text: str) -> tuple[time, time] | None:
    """解析 "21:00-07:00" 静默时段；为空或非法返回 None（=不静默）。"""
    text = (text or "").strip()
    if not text:
        return None
    parts = _SPAN_RE.split(text, maxsplit=1)
    if len(parts) != 2:
        return None
    start, end = _parse_hhmm(parts[0]), _parse_hhmm(parts[1])
    if start is None or end is None or start == end:
        return None
    return start, end


def in_silence(now: time, span: tuple[time, time] | None) -> bool:
    """判断某个时刻是否落在静默时段内（支持跨午夜，如 21:00-07:00）。"""
    if span is None:
        return False
    start, end = span
    if start < end:
        return start <= now < end
    return now >= start or now < end


def match(text: str, keywords: list[str]) -> str | None:
    """返回命中的第一个关键词；未命中返回 None（英文词忽略大小写）。"""
    if not text:
        return None
    low = text.casefold()
    for kw in keywords or []:
        if kw and kw.casefold() in low:
            return kw
    return None


def check(text: str, keywords: list[str], silence: str = "",
          now: datetime | None = None) -> str | None:
    """完整判断：静默时段内一律不提醒，否则返回命中的关键词。"""
    if in_silence((now or datetime.now()).time(), parse_silence(silence)):
        return None
    return match(text, keywords)


class AlertGate:
    """告警节流：同一 (群, 关键词) 在冷却时间内只提醒一次。

    群消息刷屏时同一个词会连续命中，没有节流会弹一屏托盘通知。
    """

    def __init__(self, cooldown_sec: float = 600.0):
        self.cooldown_sec = float(cooldown_sec)
        self._last: dict[tuple[str, str], float] = {}

    def allow(self, chatroom: str, keyword: str,
              now_ts: float | None = None) -> bool:
        """允许提醒则返回 True 并记录时间；冷却期内返回 False。"""
        import time as _time
        now = _time.time() if now_ts is None else now_ts
        key = (chatroom, keyword)
        last = self._last.get(key)
        if last is not None and now - last < self.cooldown_sec:
            return False
        self._last[key] = now
        return True

    def reset(self) -> None:
        self._last.clear()

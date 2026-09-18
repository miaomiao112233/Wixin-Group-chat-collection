# -*- coding: utf-8 -*-
"""总结管线：分块(map) → 合并(reduce) → JSON 兜底解析 → SummaryResult。"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field

from app.config import CHUNK_MAX_CHARS, CHUNK_MAX_MESSAGES
from app.models import (ActionItem, ActiveUser, Message, NotableQuote,
                        SummaryResult, Topic)
from app.summary.deepseek_client import DeepSeekClient
from app.summary.prompts import MAP_PROMPT, REDUCE_PROMPT

_JSON_RE = re.compile(r"\{.*\}", re.S)


@dataclass
class Stats:
    """统计概览——纯代码计算，不经模型。"""

    date: str
    group_name: str
    range_text: str = ""
    total: int = 0
    people: int = 0
    file_count: int = 0
    type_counter: Counter = field(default_factory=Counter)
    top_users: list[tuple[str, int]] = field(default_factory=list)

    def to_rows(self) -> list[tuple[str, str]]:
        return [
            ("统计区间", self.range_text),
            ("消息总数", str(self.total)),
            ("参与人数", str(self.people)),
            ("文件数量", str(self.file_count)),
            ("最活跃", ", ".join(f"{n}({c}条)" for n, c in self.top_users[:5])
             or "-"),
        ]


# ---------- 输入构建 ----------
def build_blocks(messages: list[Message]) -> list[str]:
    """按 [HH:MM:SS] 昵称(wxid): 内容 行格式组块（≤400条/12k字）。"""
    blocks: list[str] = []
    cur: list[str] = []
    cur_chars = 0
    for m in messages:
        line = m.line()
        if cur and (len(cur) >= CHUNK_MAX_MESSAGES
                    or cur_chars + len(line) > CHUNK_MAX_CHARS):
            blocks.append("\n".join(cur))
            cur, cur_chars = [], 0
        cur.append(line)
        cur_chars += len(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def compute_stats(messages: list[Message], group_name: str, date: str,
                  archived_files: list[dict] | None = None) -> Stats:
    s = Stats(date=date, group_name=group_name)
    if not messages:
        return s
    s.total = len(messages)
    s.people = len({m.sender_name for m in messages
                    if m.sender_wxid and m.sender_wxid != ""})
    s.file_count = sum(1 for m in messages if m.is_file)
    if archived_files:
        s.file_count = max(s.file_count,
                           sum(1 for f in archived_files
                               if f.get("status") in ("archived", "missing")))
    s.type_counter = Counter(m.msg_type for m in messages)
    cnt = Counter(m.sender_name for m in messages
                  if m.sender_wxid and m.sender_name not in ("系统",))
    s.top_users = cnt.most_common(8)
    s.range_text = (f"{messages[0].create_time:%H:%M} - "
                    f"{messages[-1].create_time:%H:%M}")
    return s


# ---------- JSON 兜底解析 ----------
def _parse_json(text: str) -> dict | None:
    m = _JSON_RE.search(text)
    if not m:
        return None
    raw = m.group(0)
    for attempt in (raw, raw.replace("，", ",").replace("：", ":")):
        try:
            data = json.loads(attempt)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    # 宽松修复：去掉尾逗号
    try:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
    except Exception:
        return None


def _to_result(data: dict) -> SummaryResult:
    r = SummaryResult()
    for t in data.get("topics") or []:
        if isinstance(t, dict) and t.get("title"):
            r.topics.append(Topic(title=str(t["title"])[:40],
                                  summary=str(t.get("summary", ""))))
    for a in data.get("action_items") or []:
        if isinstance(a, dict) and a.get("item"):
            r.action_items.append(ActionItem(
                item=str(a["item"]), owner=str(a.get("owner") or ""),
                deadline=str(a.get("deadline") or "")))
    for q in data.get("notable_quotes") or []:
        if isinstance(q, dict) and q.get("quote"):
            r.notable_quotes.append(NotableQuote(
                sender=str(q.get("sender") or ""),
                quote=str(q["quote"]), time=str(q.get("time") or "")))
    for u in data.get("active_users") or []:
        if isinstance(u, dict) and u.get("name"):
            r.active_users.append(ActiveUser(
                name=str(u["name"]), msg_count=0,
                note=str(u.get("note") or "")))
    return r


# ---------- 主入口 ----------
def summarize(client: DeepSeekClient, group_name: str,
              messages: list[Message], stats: Stats) -> SummaryResult:
    """map-reduce 总结。任何模型失败都降级为部分结果，不抛异常。"""
    result = SummaryResult()
    if not messages:
        return result
    blocks = build_blocks(messages)

    block_results: list[SummaryResult] = []
    failed = 0
    for i, block in enumerate(blocks, 1):
        prompt = MAP_PROMPT + f"（第{i}/{len(blocks)}块）\n" + block
        try:
            text = client.chat(MAP_PROMPT, prompt)
        except Exception:
            failed += 1
            continue
        data = _parse_json(text)
        if data is None:
            # 修复重试2次
            for _ in range(2):
                try:
                    text = client.chat(
                        MAP_PROMPT,
                        prompt + "\n\n注意：你上次输出不是合法 JSON，"
                                 "请严格只输出 JSON。")
                    data = _parse_json(text)
                    if data is not None:
                        break
                except Exception:
                    break
        if data is None:
            failed += 1
            continue
        block_results.append(_to_result(data))

    if not block_results:
        result.partial_failed = True
        return result

    if len(block_results) == 1:
        return block_results[0]

    try:
        merged = json.dumps(
            [_compact(r) for r in block_results], ensure_ascii=False,
            indent=1)
        text = client.chat(REDUCE_PROMPT, merged)
        data = _parse_json(text)
        if data is None:
            raise ValueError("reduce JSON 解析失败")
        result = _to_result(data)
    except Exception:
        # reduce 失败 → 降级为块结果简单拼接
        result = SummaryResult(partial_failed=True)
        for br in block_results:
            result.topics.extend(br.topics)
            result.action_items.extend(br.action_items)
            result.notable_quotes.extend(br.notable_quotes)
            result.active_users.extend(br.active_users)
    if failed:
        result.partial_failed = True
    return result


def _compact(r: SummaryResult) -> dict:
    return {
        "topics": [{"title": t.title, "summary": t.summary}
                   for t in r.topics],
        "action_items": [{"item": a.item, "owner": a.owner,
                          "deadline": a.deadline} for a in r.action_items],
        "notable_quotes": [{"sender": q.sender, "quote": q.quote,
                            "time": q.time} for q in r.notable_quotes],
        "active_users": [{"name": u.name, "note": u.note}
                         for u in r.active_users],
    }

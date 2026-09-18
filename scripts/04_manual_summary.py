# -*- coding: utf-8 -*-
"""阶段3验收：命令行手动总结（归档 + 统计 + 可选 AI 总结 + Word 生成）。

用法:
    python scripts/04_manual_summary.py "群名关键词" [--date 2026-09-14] [--no-ai]

不带 --no-ai 时需要 DeepSeek Key：
    环境变量 DEEPSEEK_API_KEY 或 data/config.json:
        {"deepseek_api_key": "sk-..."}
"""
import argparse
import io
import sys
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

from wechatauto import WeChatDB

from app.config import OUTPUT_DIR, get_deepseek_api_key
from app.core.archiver import Archiver
from app.core.message_poller import MessagePoller
from app.core.summary_service import summarize_group
from app.state_store import StateStore
from app.summary.deepseek_client import DeepSeekClient


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("keyword", help="群名关键词")
    ap.add_argument("--date", help="总结目标日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--no-ai", action="store_true", help="跳过 AI，仅统计+归档+骨架")
    args = ap.parse_args()

    day = None
    if args.date:
        day = datetime.strptime(args.date, "%Y-%m-%d").date()
        if day > date_cls.today():
            raise SystemExit("--date 不能晚于今天")

    db = WeChatDB()
    group = next(
        (g for g in db.get_groups() if args.keyword in (g.get("name") or "")),
        None,
    )
    if group is None:
        raise SystemExit(f"未找到群: {args.keyword}")
    chatroom, gname = group["username"], group.get("name") or group["username"]
    print(f"目标群: {gname} ({chatroom})  日期: {day or '今天'}")

    store = StateStore()
    store.upsert_group(chatroom, gname)
    poller = MessagePoller(db)
    archiver = Archiver(db, store)

    client = None
    if not args.no_ai:
        key = get_deepseek_api_key()
        if not key:
            raise SystemExit(
                '未配置 DeepSeek Key。请在 data/config.json 写入\n'
                '  {"deepseek_api_key": "sk-..."}\n或设置环境变量 DEEPSEEK_API_KEY，'
                "或先用 --no-ai 验证归档/统计。")
        client = DeepSeekClient(key)

    msgs = poller.fetch_day(chatroom, day)
    print(f"当日消息 {len(msgs)} 条"
          + (f"，其中文件 {sum(1 for m in msgs if m.is_file)} 条"
             if msgs else ""))
    if not msgs:
        raise SystemExit("该日无消息，无法总结")

    for m in msgs[:8]:
        print(f"  [{m.create_time:%H:%M}] {m.sender_name}: "
              f"{m.content[:36].replace(chr(10), ' ')}")

    out = summarize_group(db, poller, archiver, store, client,
                          chatroom, gname, day, OUTPUT_DIR)
    print(f"\n[完成] docx: {out}")
    if OUTPUT_DIR.exists():
        for p in sorted(OUTPUT_DIR.rglob("*")):
            if p.is_file():
                print(f"  输出文件: {p}  ({p.stat().st_size}B)")
    print("[归档记录]")
    the_date = args.date or f"{date_cls.today():%Y-%m-%d}"
    for f in store.list_archived_files(chatroom, the_date):
        print(f"  [{f['status']}] {f['orig_name']} -> {f['saved_path'] or '-'}")


if __name__ == "__main__":
    main()

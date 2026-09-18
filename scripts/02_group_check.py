# -*- coding: utf-8 -*-
"""阶段1验证脚本2：群消息/增量游标/文件关联验证。

用法:
    python scripts/02_group_check.py "群名关键词"
"""
import io
import os
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from wechatauto import WeChatDB


def find_group(db: WeChatDB, keyword: str) -> dict:
    for g in db.get_groups():
        name = g.get("name") or ""
        if keyword in name:
            return g
    raise SystemExit(f"未找到群: {keyword}")


def main() -> None:
    keyword = sys.argv[1] if len(sys.argv) > 1 else "325"
    db = WeChatDB()
    g = find_group(db, keyword)
    user = g["username"]
    print(f"目标群: {g.get('name')}  ({user})")

    print("=" * 60)
    print("[1/3] 最近10条消息:")
    msgs = db.get_messages(user, limit=10)
    for m in msgs:
        print(f"  seq={m['sort_seq']} [{time.strftime('%m-%d %H:%M', time.localtime(m['create_time']))}] "
              f"{db.get_nickname(m.get('sender_username') or '') or m.get('sender_username') or m.get('sender_id')}: "
              f"{str(m['content'])[:50].replace(chr(10), ' ')}")

    print("=" * 60)
    print("[2/3] 增量游标验证 (get_new_messages since_seq=最后一条seq):")
    last_seq = msgs[-1]["sort_seq"] if msgs else 0
    new1 = db.get_new_messages(user, since_seq=last_seq)
    print(f"  since_seq={last_seq} -> 新消息 {len(new1)} 条 (预期0)")
    new2 = db.get_new_messages(user, since_seq=0, limit=5)
    print(f"  since_seq=0 limit=5 -> {len(new2)} 条 (预期5), 首条seq={new2[0]['sort_seq'] if new2 else '-'}")

    print("=" * 60)
    print("[3/3] 文件消息关联验证:")
    # 找文件消息
    file_msgs = []
    for m in db.get_messages(user, limit=200):
        if m["type"] == "文件" or (isinstance(m["content"], str) and "<appmsg" in m["content"] and "type=\"6\"" in m["content"]):
            file_msgs.append(m)
    print(f"  最近200条中文件消息: {len(file_msgs)} 条")

    conn = db._open(r"message\message_resource.db")
    rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall()
    for name, sql in rows:
        cols = re.findall(r'\n\s+"?(\w+)"?\s+\w+', sql or "")
        print(f"  表 {name}: {', '.join(cols[:14])}")
    conn.close()

    # 消息库表结构
    conn = db._open(r"message\message_0.db")
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%' LIMIT 5").fetchall()
    print(f"  message_0.db 分表样例: {[r[0] for r in rows]}")
    conn.close()

    # msg/file 与 attach 目录
    db_dir = db.db_dir
    for sub in ("msg\\file", "msg\\attach"):
        p = os.path.join(db_dir, sub)
        if os.path.isdir(p):
            subs = os.listdir(p)[:6]
            print(f"  {sub}/ 存在, 子目录样例: {subs}")
        else:
            print(f"  {sub}/ 不存在")

    print("=" * 60)
    print("验证完成")


if __name__ == "__main__":
    main()

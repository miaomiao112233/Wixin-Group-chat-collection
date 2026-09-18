# -*- coding: utf-8 -*-
"""阶段1验证脚本：验证微信4.x 数据库读取链路（密钥提取/消息读取/文件关联）。

用法:
    python scripts/01_wechat_check.py                 # 基础检查: 账号+群列表+filehelper消息
    python scripts/01_wechat_check.py --group 群名    # 读取指定群最近20条消息
    python scripts/01_wechat_check.py --schema        # dump 数据库表结构
"""
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from wechatauto import WeChatDB


def main() -> None:
    args = sys.argv[1:]
    show_schema = "--schema" in args
    group = None
    if "--group" in args:
        group = args[args.index("--group") + 1]

    print("=" * 60)
    print("[1/4] 初始化 WeChatDB（自动提取密钥，首次约需数秒）...")
    db = WeChatDB()

    info = db.get_self_info()
    print(f"  当前账号: {info}")

    print("=" * 60)
    print("[2/4] 群聊列表（前10个）:")
    groups = db.get_groups()
    for g in groups[:10]:
        print(f"  {g.get('name') or g.get('username')}  <-  {g.get('username')}")
    print(f"  共 {len(groups)} 个群")

    print("=" * 60)
    print("[3/4] 消息读取验证:")
    target = group or "filehelper"
    msgs = db.get_messages(target, limit=20)
    print(f"  目标会话: {target}  昵称: {db.get_nickname(target)}  消息数: {len(msgs)}")
    for m in msgs[-10:]:
        print(f"  [{m.get('create_time')}] {m.get('sender_id')}: {str(m.get('content'))[:60]}")
    if not msgs:
        print("  (无消息，跳过)")

    if show_schema:
        print("=" * 60)
        print("[4/4] 表结构 dump:")
        for shard in ("message_0.db", "message_resource.db", "contact.db"):
            try:
                conn = db._open(shard)
            except Exception as e:  # noqa: BLE001
                print(f"  {shard}: 打开失败 {e}")
                continue
            rows = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' LIMIT 30"
            ).fetchall()
            print(f"  --- {shard} ---")
            for name, sql in rows:
                cols = (sql or "")[:220].replace("\n", " ")
                print(f"    {name}: {cols}")
            conn.close()
    else:
        print("=" * 60)
        print("[4/4] 文件消息样本（如有）:")
        for m in msgs:
            if m.get("type") in (6, "6") or "文件" in str(m.get("content", ""))[:6]:
                print(f"  文件消息: {m}")
                break
        else:
            print("  (最近消息中无文件消息)")

    print("=" * 60)
    print("验证完成")


if __name__ == "__main__":
    main()

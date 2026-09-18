# -*- coding: utf-8 -*-
"""阶段2验收：核心库（state_store / message_poller / interval_ctl）联测。

对真实群跑三轮增量轮询：
  - 预备轮把游标回拨 min(3, 可用条数) → 第1轮应精确捞出该数目（正向路径）
  - 第2、3轮只出新消息（应为 0）
  - 演示 interval_ctl 自适应间隔
  - 打印 state.db 落库结果

用法: python scripts/03_phase2_check.py "群名关键词"
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

from wechatauto import WeChatDB

from app.core.interval_ctl import IntervalController
from app.core.message_poller import MessagePoller
from app.state_store import StateStore


def main() -> None:
    keyword = sys.argv[1] if len(sys.argv) > 1 else "325"
    db = WeChatDB()
    group = next(
        (g for g in db.get_groups() if keyword in (g.get("name") or "")), None
    )
    if group is None:
        raise SystemExit(f"未找到群: {keyword}")
    chatroom, gname = group["username"], group.get("name") or group["username"]
    print(f"目标群: {gname} ({chatroom})")

    store = StateStore()
    store.upsert_group(chatroom, gname)
    poller = MessagePoller(db)

    # ---- 预备：游标回拨，制造正向路径 ----
    latest = poller.latest_seq(chatroom)
    back = min(3, max(1, len(db.get_messages(chatroom, limit=50))))
    start_seq = max(0, latest - back)
    store.set_cursor(chatroom, start_seq)
    print(f"\n[预备] 库内最新 seq={latest}，游标回拨到 {start_seq}（取 {back} 条）")

    # ---- 三轮增量轮询 ----
    for rnd in range(1, 4):
        cursor = store.get_cursor(chatroom)
        msgs = poller.fetch_new(chatroom, cursor)
        if msgs:
            for m in msgs:
                print(f"  第{rnd}轮 +seq={m.sort_seq} "
                      f"[{m.create_time:%m-%d %H:%M}] {m.sender_name}: "
                      f"{m.content[:40].replace(chr(10), ' ')}")
            store.advance_cursor(chatroom, msgs[-1].sort_seq)
        print(f"[第{rnd}轮] since={cursor} -> 新消息 {len(msgs)} 条, "
              f"新游标={store.get_cursor(chatroom)}")

    # ---- 发送者解析抽检（近10条） ----
    sample = poller.fetch_new(chatroom, max(0, latest - 10))
    if sample:
        print(f"\n[发送者解析抽检] 近{len(sample)}条:")
        for m in sample:
            print(f"  [{m.create_time:%m-%d %H:%M}] {m.msg_type:<4} "
                  f"{m.sender_name}({m.sender_wxid or '-'}): "
                  f"{m.content[:36].replace(chr(10), ' ')}")
        m = sample[0]
        print(f"[行格式] {m.line()[:80]}")

    # ---- interval_ctl 演示 ----
    ctl = IntervalController()
    print(f"\n[interval_ctl] 初始 {ctl.interval_sec/60:.1f}min")
    for n in (0, 5, 0, 250, 30, 0, 0):
        ctl.on_round(n)
        print(f"  新增 {n:>3} -> 间隔 {ctl.interval_sec/60:.1f}min, "
              f"下次 {ctl.next_run_time():%H:%M}")
    print(f"  强制点检查(今天已总结=False) -> "
          f"{ctl.should_force_summary(None)}; "
          f"下次强制时刻 {ctl.next_force_time():%m-%d %H:%M}")

    # ---- state.db 落库结果 ----
    print("\n[state.db] monitor_group:")
    for g in store.list_groups():
        print(f"  {g}")
    store.log_error("INFO", "phase2_check", "验收脚本跑通")

    # ---- 清理演示数据：游标恢复到最新 ----
    store.advance_cursor(chatroom, latest)
    print(f"\n[收尾] 游标恢复到最新 {latest}，验收完成")


if __name__ == "__main__":
    main()

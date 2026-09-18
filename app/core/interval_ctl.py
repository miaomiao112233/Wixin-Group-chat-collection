# -*- coding: utf-8 -*-
"""自适应总结间隔状态机。

规则（见实现计划 §核心设计决策3）：
  上轮新增 n：
    n == 0        → 间隔 ×1.5
    0 < n < 20    → 间隔 ×1.2
    20 ≤ n < 200  → 保持
    n ≥ 200       → 间隔 ×0.7
  间隔钳制在 [15min, 60min]；另挂每日 23:30 强制总结点。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.config import (DAILY_FORCE_TIME, SUMMARY_BURST_THRESHOLD,
                        SUMMARY_FEW_THRESHOLD, SUMMARY_GROW_FEW,
                        SUMMARY_GROW_IDLE, SUMMARY_MAX_INTERVAL_SEC,
                        SUMMARY_MIN_INTERVAL_SEC, SUMMARY_SHRINK_BURST)


class IntervalController:
    def __init__(self,
                 min_sec: int = SUMMARY_MIN_INTERVAL_SEC,
                 max_sec: int = SUMMARY_MAX_INTERVAL_SEC,
                 force_time: str = DAILY_FORCE_TIME):
        self.min_sec = float(min_sec)
        self.max_sec = float(max_sec)
        self.force_time = force_time
        self._interval = float(min_sec)

    # ---------- 状态 ----------
    @property
    def interval_sec(self) -> float:
        return self._interval

    def reset(self) -> None:
        self._interval = self.min_sec

    # ---------- 反馈 ----------
    def on_round(self, new_count: int) -> None:
        if new_count == 0:
            factor = SUMMARY_GROW_IDLE
        elif new_count < SUMMARY_FEW_THRESHOLD:
            factor = SUMMARY_GROW_FEW
        elif new_count >= SUMMARY_BURST_THRESHOLD:
            factor = SUMMARY_SHRINK_BURST
        else:
            factor = 1.0
        self._interval = min(self.max_sec,
                             max(self.min_sec, self._interval * factor))

    # ---------- 调度 ----------
    def next_run_time(self, now: datetime | None = None) -> datetime:
        return (now or datetime.now()) + timedelta(seconds=self._interval)

    def next_force_time(self, now: datetime | None = None) -> datetime:
        """今天（或已过点则明天）的强制总结时刻。"""
        now = now or datetime.now()
        hh, mm = self.force_time.split(":")
        t = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if t <= now:
            t += timedelta(days=1)
        return t

    def should_force_summary(self, last_summary_at: str | None,
                             now: datetime | None = None) -> bool:
        """今日 23:30 已过且今天还没总结过 → 强制。

        last_summary_at 格式 "%Y-%m-%d %H:%M:%S"（state.db 存储格式）。
        """
        now = now or datetime.now()
        hh, mm = self.force_time.split(":")
        cutoff = now.replace(hour=int(hh), minute=int(mm),
                             second=0, microsecond=0)
        if now < cutoff:
            return False
        if not last_summary_at:
            return True
        try:
            last = datetime.strptime(last_summary_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return True
        return last.date() < now.date()

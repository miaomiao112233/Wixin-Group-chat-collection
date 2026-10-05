# -*- coding: utf-8 -*-
"""app.core.interval_ctl.IntervalController 的单元测试。

测什么：自适应总结间隔状态机——无消息放大、少量放大、刷屏缩短、阈值
边界（SUMMARY_FEW_THRESHOLD / SUMMARY_BURST_THRESHOLD）、上下限夹紧、
reset、下次排程与每日强制点。
为什么：这段逻辑决定总结频率，阈值与夹紧的差一错误会直接改变产品行为。
时间相关的断言全部用注入的 ``now``，不依赖当前时刻。
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from app.config import (DAILY_FORCE_TIME, SUMMARY_BURST_THRESHOLD,
                        SUMMARY_FEW_THRESHOLD, SUMMARY_GROW_FEW,
                        SUMMARY_GROW_IDLE, SUMMARY_MAX_INTERVAL_SEC,
                        SUMMARY_MIN_INTERVAL_SEC, SUMMARY_SHRINK_BURST)
from app.core.interval_ctl import IntervalController


class IntervalControllerTest(unittest.TestCase):
    """间隔状态机行为（15–60 分钟 + 每日强制点）。"""

    # ---------- 常量与初始状态 ----------
    def test_配置常量与设计区间一致(self) -> None:
        """测什么：夹紧范围与阈值常量。为什么：边界断言依赖这些值。"""
        self.assertEqual(SUMMARY_MIN_INTERVAL_SEC, 15 * 60)
        self.assertEqual(SUMMARY_MAX_INTERVAL_SEC, 60 * 60)
        self.assertEqual(SUMMARY_FEW_THRESHOLD, 20)
        self.assertEqual(SUMMARY_BURST_THRESHOLD, 200)
        self.assertEqual(DAILY_FORCE_TIME, "23:30")

    def test_初始间隔等于下限且为浮点数(self) -> None:
        """测什么：新实例从下限起步。为什么：首轮总结间隔=最小值。"""
        ctl = IntervalController()
        self.assertEqual(ctl.interval_sec, float(SUMMARY_MIN_INTERVAL_SEC))
        self.assertEqual(ctl.min_sec, float(SUMMARY_MIN_INTERVAL_SEC))
        self.assertEqual(ctl.max_sec, float(SUMMARY_MAX_INTERVAL_SEC))
        self.assertEqual(ctl.force_time, DAILY_FORCE_TIME)

    def test_reset_回到下限(self) -> None:
        """测什么：reset 丢弃历史反馈。为什么：手动重置后应最快总结。"""
        ctl = IntervalController()
        ctl.on_round(0)
        self.assertNotEqual(ctl.interval_sec, float(SUMMARY_MIN_INTERVAL_SEC))
        ctl.reset()
        self.assertEqual(ctl.interval_sec, float(SUMMARY_MIN_INTERVAL_SEC))

    def test_interval_sec_是只读属性(self) -> None:
        """测什么：外部不能直接改间隔。为什么：只能经受控反馈变更。"""
        ctl = IntervalController()
        with self.assertRaises(AttributeError):
            ctl.interval_sec = 123.0  # type: ignore[misc]

    # ---------- 三档反馈 ----------
    def test_无新消息按放大系数增长(self) -> None:
        """测什么：n==0 走 SUMMARY_GROW_IDLE。为什么：无消息应拉长间隔。"""
        ctl = IntervalController()
        ctl.on_round(0)
        self.assertAlmostEqual(
            ctl.interval_sec,
            SUMMARY_MIN_INTERVAL_SEC * SUMMARY_GROW_IDLE, places=6)

    def test_少量新消息按略放大系数增长(self) -> None:
        """测什么：0<n<20 走 SUMMARY_GROW_FEW。为什么：低频群省调用。"""
        for n in (1, 5, SUMMARY_FEW_THRESHOLD - 1):
            with self.subTest(n=n):
                ctl = IntervalController()
                ctl.on_round(n)
                self.assertAlmostEqual(
                    ctl.interval_sec,
                    SUMMARY_MIN_INTERVAL_SEC * SUMMARY_GROW_FEW, places=6)

    def test_阈值_20_条时保持间隔不变(self) -> None:
        """测什么：n==SUMMARY_FEW_THRESHOLD 属于"保持"档。

        为什么：20 是放大档的上边界（不含），差一即改变行为。
        """
        ctl = IntervalController()
        ctl.on_round(SUMMARY_FEW_THRESHOLD)
        self.assertEqual(ctl.interval_sec, float(SUMMARY_MIN_INTERVAL_SEC))

    def test_阈值_200_条起才缩短(self) -> None:
        """测什么：n==199 保持，n==SUMMARY_BURST_THRESHOLD 才缩短。

        为什么：刷屏判据的下边界，差一即改变行为。
        """
        keep = IntervalController()
        keep.on_round(0)                     # 先抬到上限，便于观察缩短
        keep.on_round(0)
        keep.on_round(0)
        keep.on_round(0)
        self.assertEqual(keep.interval_sec, float(SUMMARY_MAX_INTERVAL_SEC))
        keep.on_round(SUMMARY_BURST_THRESHOLD - 1)
        self.assertEqual(keep.interval_sec, float(SUMMARY_MAX_INTERVAL_SEC))

        shrink = IntervalController()
        shrink.on_round(0)
        shrink.on_round(0)
        shrink.on_round(0)
        shrink.on_round(0)
        shrink.on_round(SUMMARY_BURST_THRESHOLD)
        self.assertAlmostEqual(
            shrink.interval_sec,
            SUMMARY_MAX_INTERVAL_SEC * SUMMARY_SHRINK_BURST, places=6)

    def test_刷屏缩短在下限处夹紧(self) -> None:
        """测什么：已在下限时刷屏不越界。为什么：间隔不得小于 15 分钟。"""
        ctl = IntervalController()
        for _ in range(5):
            ctl.on_round(SUMMARY_BURST_THRESHOLD)
        self.assertEqual(ctl.interval_sec, float(SUMMARY_MIN_INTERVAL_SEC))

    def test_连续刷屏逐级缩短到下限(self) -> None:
        """测什么：从上限按 0.7 连乘并在 15 分钟处夹紧。"""
        ctl = IntervalController()
        for _ in range(4):                   # 空闲 4 轮到达上限
            ctl.on_round(0)
        self.assertEqual(ctl.interval_sec, float(SUMMARY_MAX_INTERVAL_SEC))
        expected = float(SUMMARY_MAX_INTERVAL_SEC)
        for _ in range(3):
            ctl.on_round(SUMMARY_BURST_THRESHOLD)
            expected *= SUMMARY_SHRINK_BURST
            self.assertAlmostEqual(ctl.interval_sec, expected, places=6)
        ctl.on_round(SUMMARY_BURST_THRESHOLD)   # 第 4 次会越过下限
        self.assertEqual(ctl.interval_sec, float(SUMMARY_MIN_INTERVAL_SEC))

    def test_连续空闲在上限处夹紧(self) -> None:
        """测什么：空闲放大不得越过 60 分钟。为什么：上限是产品约束。"""
        ctl = IntervalController()
        for _ in range(10):
            ctl.on_round(0)
        self.assertEqual(ctl.interval_sec, float(SUMMARY_MAX_INTERVAL_SEC))

    def test_自定义上下限同样夹紧(self) -> None:
        """测什么：min/max 可注入且夹紧生效。为什么：便于测试与复用。"""
        ctl = IntervalController(min_sec=100, max_sec=200)
        self.assertEqual(ctl.interval_sec, 100.0)
        ctl.on_round(0)                      # 100*1.5=150，未越界
        self.assertAlmostEqual(ctl.interval_sec, 150.0, places=6)
        ctl.on_round(0)                      # 150*1.5=225 → 夹到 200
        self.assertEqual(ctl.interval_sec, 200.0)
        ctl.on_round(0)
        self.assertEqual(ctl.interval_sec, 200.0)
        ctl.on_round(SUMMARY_BURST_THRESHOLD)   # 200*0.7=140
        self.assertAlmostEqual(ctl.interval_sec, 140.0, places=6)
        ctl.on_round(SUMMARY_BURST_THRESHOLD)   # 98 → 夹到 100
        self.assertEqual(ctl.interval_sec, 100.0)

    # ---------- 排程 ----------
    def test_下次运行时间按当前间隔推算(self) -> None:
        """测什么：next_run_time = now + interval_sec。为什么：排程依据。"""
        now = datetime(2025, 3, 1, 10, 0, 0)
        ctl = IntervalController()
        self.assertEqual(
            ctl.next_run_time(now),
            now + timedelta(seconds=float(SUMMARY_MIN_INTERVAL_SEC)))
        ctl.on_round(0)
        self.assertEqual(ctl.next_run_time(now),
                         now + timedelta(seconds=ctl.interval_sec))

    def test_下次运行时间默认取当前时刻(self) -> None:
        """测什么：不传 now 时以真实当前时刻为基准。

        为什么：默认分支也要可用；断言放宽避免依赖精确时刻。
        """
        ctl = IntervalController()
        low = datetime.now()
        result = ctl.next_run_time()
        high = datetime.now()
        self.assertGreaterEqual(result, low + timedelta(seconds=899))
        self.assertLessEqual(result, high + timedelta(seconds=901))

    def test_每日强制点_当天未到取当天(self) -> None:
        """测什么：23:30 之前取当天 23:30 且秒/微秒归零。"""
        ctl = IntervalController()
        now = datetime(2025, 3, 1, 10, 0, 0, 123456)
        self.assertEqual(ctl.next_force_time(now),
                         datetime(2025, 3, 1, 23, 30, 0, 0))

    def test_每日强制点_已到或已过取次日(self) -> None:
        """测什么：到点即视为已过（<= now 顺延一天）。"""
        ctl = IntervalController()
        self.assertEqual(ctl.next_force_time(datetime(2025, 3, 1, 23, 30)),
                         datetime(2025, 3, 2, 23, 30))
        self.assertEqual(
            ctl.next_force_time(datetime(2025, 3, 1, 23, 31)),
            datetime(2025, 3, 2, 23, 30))
        self.assertEqual(
            ctl.next_force_time(datetime(2025, 12, 31, 23, 59)),
            datetime(2026, 1, 1, 23, 30))

    def test_每日强制点_可注入时刻(self) -> None:
        """测什么：force_time 参数生效。为什么：设置项可改强制点。"""
        ctl = IntervalController(force_time="00:05")
        self.assertEqual(ctl.next_force_time(datetime(2025, 3, 1, 0, 4)),
                         datetime(2025, 3, 1, 0, 5))
        self.assertEqual(ctl.next_force_time(datetime(2025, 3, 1, 6, 0)),
                         datetime(2025, 3, 2, 0, 5))


if __name__ == "__main__":          # pragma: no cover
    unittest.main()

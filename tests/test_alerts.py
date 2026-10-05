# -*- coding: utf-8 -*-
"""关键词告警纯逻辑测试：词表/静默时段解析、命中判断、告警节流。"""
from __future__ import annotations

import unittest
from datetime import datetime, time

from app.core.alerts import (AlertGate, check, format_keywords, in_silence,
                             match, parse_keywords, parse_silence)


class ParseKeywordsTest(unittest.TestCase):
    def test_中英文逗号分号与空白都当分隔符(self):
        self.assertEqual(parse_keywords("作业,考试；会议 通知、报名"),
                         ["作业", "考试", "会议", "通知", "报名"])

    def test_去重且保持顺序(self):
        self.assertEqual(parse_keywords("作业,作业,考试,作业"),
                         ["作业", "考试"])

    def test_空与None返回空表(self):
        self.assertEqual(parse_keywords(""), [])
        self.assertEqual(parse_keywords(None), [])

    def test_成对往返(self):
        words = ["作业", "考试", "会议"]
        self.assertEqual(parse_keywords(format_keywords(words)), words)


class ParseSilenceTest(unittest.TestCase):
    def test_普通时段(self):
        self.assertEqual(parse_silence("22:00-07:00"),
                         (time(22, 0), time(7, 0)))

    def test_支持波浪线与中文连接词(self):
        self.assertEqual(parse_silence("22:00~07:00"),
                         (time(22, 0), time(7, 0)))
        self.assertEqual(parse_silence("22:00到07:00"),
                         (time(22, 0), time(7, 0)))

    def test_空与非法返回None(self):
        for text in ("", "   ", "22:00", "25:00-07:00", "22:00-07:70",
                     "09:00-09:00", "abc"):
            self.assertIsNone(parse_silence(text), text)

    def test_兼容一位小时(self):
        self.assertEqual(parse_silence("9:05-18:30"),
                         (time(9, 5), time(18, 30)))


class InSilenceTest(unittest.TestCase):
    def test_不跨午夜(self):
        span = parse_silence("09:00-18:00")
        self.assertTrue(in_silence(time(12, 0), span))
        self.assertFalse(in_silence(time(8, 59), span))
        self.assertFalse(in_silence(time(18, 0), span))   # 右开区间

    def test_跨午夜(self):
        span = parse_silence("21:00-07:00")
        self.assertTrue(in_silence(time(23, 30), span))
        self.assertTrue(in_silence(time(3, 0), span))
        self.assertFalse(in_silence(time(12, 0), span))
        self.assertFalse(in_silence(time(7, 0), span))

    def test_None表示不静默(self):
        self.assertFalse(in_silence(time(3, 0), None))


class MatchTest(unittest.TestCase):
    def test_命中返回关键词(self):
        self.assertEqual(match("明天交作业", ["作业", "考试"]), "作业")

    def test_英文忽略大小写(self):
        self.assertEqual(match("Deadline is Friday", ["deadline"]), "deadline")

    def test_未命中返回None(self):
        self.assertIsNone(match("随便聊聊", ["作业"]))
        self.assertIsNone(match("", ["作业"]))
        self.assertIsNone(match("作业", []))


class CheckTest(unittest.TestCase):
    def test_静默时段内不提醒(self):
        now = datetime(2026, 1, 6, 23, 0)
        self.assertIsNone(check("明天交作业", ["作业"], "21:00-07:00", now))

    def test_非静默时段正常提醒(self):
        now = datetime(2026, 1, 6, 12, 0)
        self.assertEqual(check("明天交作业", ["作业"], "21:00-07:00", now),
                         "作业")

    def test_时段非法时视为不静默(self):
        now = datetime(2026, 1, 6, 23, 0)
        self.assertEqual(check("开会", ["开会"], "乱写", now), "开会")


class AlertGateTest(unittest.TestCase):
    def test_冷却期内只放行一次(self):
        gate = AlertGate(cooldown_sec=600)
        self.assertTrue(gate.allow("g1", "作业", now_ts=1000.0))
        self.assertFalse(gate.allow("g1", "作业", now_ts=1300.0))
        self.assertTrue(gate.allow("g1", "作业", now_ts=1601.0))

    def test_不同群或不同词互不影响(self):
        gate = AlertGate(cooldown_sec=600)
        self.assertTrue(gate.allow("g1", "作业", now_ts=1000.0))
        self.assertTrue(gate.allow("g2", "作业", now_ts=1000.0))
        self.assertTrue(gate.allow("g1", "考试", now_ts=1000.0))

    def test_reset清空(self):
        gate = AlertGate(cooldown_sec=600)
        gate.allow("g1", "作业", now_ts=1000.0)
        gate.reset()
        self.assertTrue(gate.allow("g1", "作业", now_ts=1000.0))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""app.models 数据类的基础行为测试（构造、默认值、派生属性）。

测什么：Message 的默认值与 line()/is_file 派生行为、Group/FileInfo 的
默认值、四模块结果数据类的默认值、SummaryResult 的容器字段是否各自独立
（default_factory 而非共享可变默认值）。
为什么：line() 的格式是喂给模型的输入契约；共享默认可变对象会让两个群
的总结互相污染。
"""
from __future__ import annotations

import unittest
from dataclasses import is_dataclass
from datetime import datetime

from app.models import (ActionItem, ActiveUser, FileInfo, Group, Message,
                        NotableQuote, SummaryResult, Topic)

BASE_TIME = datetime(2025, 1, 2, 9, 5, 7)


def make_message(wxid: str = "wxid_a",
                 when: datetime = BASE_TIME) -> Message:
    """构造一条字段齐全的消息。"""
    return Message(chatroom_id="chatroom_1", sort_seq=7, local_id=8,
                   msg_type="文本", sender_wxid=wxid, sender_name="张三",
                   content="早上好", create_time=when)


class MessageTest(unittest.TestCase):
    """Message 的构造、默认值与派生属性。"""

    def test_可选字段默认空串(self) -> None:
        """测什么：raw_content/file_name/svr_id 默认空串。"""
        message = make_message()
        self.assertEqual(message.raw_content, "")
        self.assertEqual(message.file_name, "")
        self.assertEqual(message.svr_id, "")

    def test_is_file_只由文件名决定(self) -> None:
        """测什么：is_file 等价于 file_name 非空。"""
        self.assertFalse(make_message().is_file)
        message = make_message()
        message.file_name = "报告.pdf"
        self.assertTrue(message.is_file)

    def test_line_格式与补零(self) -> None:
        """测什么：行格式为 [HH:MM:SS] 昵称(wxid): 内容。"""
        self.assertEqual(make_message().line(),
                         "[09:05:07] 张三(wxid_a): 早上好")

    def test_line_无_wxid_时显示系统(self) -> None:
        """测什么：发送者 wxid 为空时占用位是"系统"。"""
        self.assertEqual(make_message(wxid="").line(),
                         "[09:05:07] 张三(系统): 早上好")

    def test_数据类相等性(self) -> None:
        """测什么：同字段值的两条消息相等（dataclass 语义）。"""
        self.assertTrue(is_dataclass(Message))
        self.assertEqual(make_message(), make_message())


class SimpleModelTest(unittest.TestCase):
    """Group / FileInfo / 四模块数据类的默认值。"""

    def test_group_默认启用(self) -> None:
        """测什么：Group.enabled 默认 True。"""
        self.assertTrue(Group("c1", "甲群").enabled)
        self.assertFalse(Group("c1", "甲群", enabled=False).enabled)

    def test_file_info_默认值(self) -> None:
        """测什么：FileInfo 全字段默认值，状态默认 pending。"""
        info = FileInfo()
        self.assertEqual((info.chatroom_id, info.date, info.orig_name,
                          info.saved_path, info.size, info.svr_id),
                         ("", "", "", "", 0, ""))
        self.assertEqual(info.status, "pending")

    def test_四模块数据类默认值(self) -> None:
        """测什么：Topic/ActiveUser/NotableQuote/ActionItem 的可选字段。"""
        self.assertEqual(Topic("标题", "摘要").summary, "摘要")
        self.assertEqual(ActiveUser("张三", 3).msg_count, 3)
        self.assertEqual(ActiveUser("张三", 3).note, "")
        self.assertEqual(NotableQuote("张三", "金句").time, "")
        self.assertEqual(ActionItem("干活").owner, "")
        self.assertEqual(ActionItem("干活").deadline, "")


class SummaryResultTest(unittest.TestCase):
    """SummaryResult 的默认值与容器独立性。"""

    def test_默认空且未标记失败(self) -> None:
        """测什么：新结果的四个列表为空、partial_failed 为 False。"""
        result = SummaryResult()
        self.assertEqual(result.topics, [])
        self.assertEqual(result.active_users, [])
        self.assertEqual(result.notable_quotes, [])
        self.assertEqual(result.action_items, [])
        self.assertFalse(result.partial_failed)

    def test_默认列表互不共享(self) -> None:
        """测什么：两个实例的列表字段彼此独立。

        为什么：若用可变默认值，追加一个群的结果会污染另一个群。
        """
        first, second = SummaryResult(), SummaryResult()
        first.topics.append(Topic("t", "s"))
        first.action_items.append(ActionItem("i"))
        self.assertEqual(second.topics, [])
        self.assertEqual(second.action_items, [])

    def test_相等性包含失败标记(self) -> None:
        """测什么：partial_failed 参与 dataclass 相等判断。"""
        self.assertEqual(SummaryResult(), SummaryResult())
        self.assertNotEqual(SummaryResult(partial_failed=True),
                            SummaryResult())


if __name__ == "__main__":          # pragma: no cover
    unittest.main()

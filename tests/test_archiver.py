# -*- coding: utf-8 -*-
"""app.core.archiver 的纯逻辑测试（safe_name / _expect_size / 名字变体）。

测什么：目录名清洗（非法字符、保留设备名、目录穿越、长度），归档判据
_expect_size 从原始 XML 提取 totallen 的成败路径，以及 _name_variants
枚举微信同名变体 ((1)/(2)…) 的规则。
为什么：群名来自微信库可被外部影响，目录名清洗是防穿越的第一道闸；
大小判据决定"张冠李戴"是否会再次发生。
不实例化 Archiver（构造需要 WeChatDB，会碰真实微信数据），只调用模块级
函数与静态方法。文件操作用独立临时目录。
"""
from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path

from app.core.archiver import Archiver, safe_name
from app.models import Message
from tests.support import cleanup_dir, make_temp_dir

BASE_TIME = datetime(2025, 1, 2, 9, 0, 0)


def make_message(raw_content: str = "", file_name: str = "a.pdf") -> Message:
    """构造一条文件消息（只关心 raw_content 与文件名）。"""
    return Message(chatroom_id="chatroom_1", sort_seq=1, local_id=1,
                   msg_type="文件", sender_wxid="wxid_a", sender_name="张三",
                   content="[文件] a.pdf", create_time=BASE_TIME,
                   raw_content=raw_content, file_name=file_name)


class SafeNameTest(unittest.TestCase):
    """safe_name：非法字符替换与危险名处理。"""

    def test_非法字符替换为下划线(self) -> None:
        """测什么：九个文件系统非法字符全部替换。"""
        self.assertEqual(safe_name('a/b\\c:d*e?f"g<h>i|j'),
                         "a_b_c_d_e_f_g_h_i_j")

    def test_首尾空白与结尾点空格被去掉(self) -> None:
        """测什么：Windows 不允许目录名以点或空格结尾。"""
        self.assertEqual(safe_name("  群名  "), "群名")
        self.assertEqual(safe_name("群名... "), "群名")
        self.assertEqual(safe_name("群名."), "群名")
        self.assertEqual(safe_name("\t群名\n"), "群名")

    def test_空名与点号名回退为未知群(self) -> None:
        """测什么：空串、纯空白、纯点、".." 都归一到安全默认名。"""
        for name in ("", "   ", "...", "..", ". . "):
            with self.subTest(name=name):
                self.assertEqual(safe_name(name), "未知群")

    def test_目录穿越被消除(self) -> None:
        """测什么：'..' 与含分隔符的穿越串不能活下来。"""
        self.assertEqual(safe_name(".."), "未知群")
        self.assertEqual(safe_name("..\\..\\Windows"), ".._.._Windows")
        self.assertNotIn("/", safe_name("../../etc/passwd"))

    def test_纯非法字符变成下划线而非未知群(self) -> None:
        """测什么：替换后仍有内容，就保留下划线形式。"""
        self.assertEqual(safe_name("///"), "___")

    def test_windows_保留设备名加前缀(self) -> None:
        """测什么：CON/PRN/NUL/COM1-9/LPT1-9 加下划线前缀，且大小写无关。"""
        for name in ("CON", "con", "PRN", "AUX", "NUL", "COM1", "com9",
                     "LPT1", "lpt9"):
            with self.subTest(name=name):
                self.assertEqual(safe_name(name), f"_{name}")

    def test_带扩展名的保留名不处理_现状记录(self) -> None:
        """测什么：'CON.txt' 不判定为保留名（现状）。

        为什么：Windows 上 CON.txt 同样是设备名，此处固化现状，
        便于将来改为按主名判断。
        """
        self.assertEqual(safe_name("CON.txt"), "CON.txt")

    def test_超长群名不截断_现状记录(self) -> None:
        """测什么：safe_name 不做长度截断（现状）。

        为什么：文档字符串只承诺字符清洗，没有长度上限；超长群名会
        直接变成超长目录名（Windows 路径长度风险），此处固化现状。
        """
        long_name = "甲" * 300
        self.assertEqual(safe_name(long_name), long_name)
        self.assertEqual(len(safe_name(long_name)), 300)


class ExpectSizeTest(unittest.TestCase):
    """_expect_size：从原始 XML 提取 totallen（取不到返回 -1）。"""

    def test_提取_totallen(self) -> None:
        """测什么：appmsg 里的 totallen 被解析为整数。"""
        xml = "<msg><appmsg><totallen>12345</totallen></appmsg></msg>"
        self.assertEqual(Archiver._expect_size(make_message(xml)), 12345)

    def test_剥离微信前缀(self) -> None:
        """测什么：'wxid:\n' 形式的原始前缀先去掉再解析。"""
        xml = "<msg><appmsg><totallen>99</totallen></appmsg></msg>"
        self.assertEqual(Archiver._expect_size(
            make_message(f"wxid_abc123:\n{xml}")), 99)
        self.assertEqual(Archiver._expect_size(
            make_message(f"张三:\n{xml}")), 99)

    def test_深层嵌套也能取到(self) -> None:
        """测什么：任意层级的 totallen 都能找到。"""
        xml = "<a><b><c><totallen>5</totallen></c></b></a>"
        self.assertEqual(Archiver._expect_size(make_message(xml)), 5)

    def test_取不到时返回负一(self) -> None:
        """测什么：空内容/无该字段/空文本/非数字/非 XML 一律 -1。

        为什么：-1 表示"跳过大小校验"，不能误判为 0 字节文件。
        """
        cases = {
            "空内容": "",
            "无 totallen": "<msg></msg>",
            "空文本": "<msg><totallen></totallen></msg>",
            "非数字": "<msg><totallen>abc</totallen></msg>",
            "非 XML": "not xml at all",
            "只有前缀": "wxid_abc:\n",
        }
        for label, raw in cases.items():
            with self.subTest(case=label):
                self.assertEqual(Archiver._expect_size(make_message(raw)), -1)


class NameVariantsTest(unittest.TestCase):
    """_name_variants：原名 + 微信重名变体的枚举顺序。"""

    def setUp(self) -> None:
        self.tmp = make_temp_dir()
        self.addCleanup(cleanup_dir, self.tmp)

    def touch(self, name: str) -> Path:
        """在临时目录里建一个占位文件。"""
        path = self.tmp / name
        path.write_text("x", encoding="utf-8")
        return path

    def test_原名优先且变体按编号升序(self) -> None:
        """测什么：返回顺序 = 原名, (1), (2)（与创建顺序无关）。"""
        self.touch("报告(2).docx")
        self.touch("报告.docx")
        self.touch("报告(1).docx")
        self.touch("别的.docx")
        found = Archiver._name_variants(self.tmp, "报告.docx")
        self.assertEqual([p.name for p in found],
                         ["报告.docx", "报告(1).docx", "报告(2).docx"])

    def test_编号不连续时只回存在的变体(self) -> None:
        """测什么：只有 (2) 没有 (1) 也能枚举到（实测存在此情况）。"""
        self.touch("报告(2).docx")
        found = Archiver._name_variants(self.tmp, "报告.docx")
        self.assertEqual([p.name for p in found], ["报告(2).docx"])

    def test_没有原名时只回变体(self) -> None:
        """测什么：原名已删除只有变体时仍能定位。"""
        self.touch("报告(3).docx")
        self.touch("报告(1).docx")
        found = Archiver._name_variants(self.tmp, "报告.docx")
        self.assertEqual([p.name for p in found],
                         ["报告(1).docx", "报告(3).docx"])

    def test_同名目录被忽略(self) -> None:
        """测什么：目录不算文件候选。为什么：避免复制到目录导致报错。"""
        (self.tmp / "报告(9).docx").mkdir()
        (self.tmp / "报告.docx").mkdir()
        self.assertEqual(Archiver._name_variants(self.tmp, "报告.docx"), [])

    def test_文件名含_glob_特殊字符按字面匹配(self) -> None:
        """测什么：方括号等字符不被当成通配符。"""
        self.touch("[草稿]报告(1).docx")
        self.touch("[草稿]报告.docx")
        found = Archiver._name_variants(self.tmp, "[草稿]报告.docx")
        self.assertEqual([p.name for p in found],
                         ["[草稿]报告.docx", "[草稿]报告(1).docx"])

    def test_多后缀文件按最后一个扩展名匹配(self) -> None:
        """测什么：'x.tar.gz' 的 stem 是 'x.tar'，变体形如 x.tar(1).gz。"""
        self.touch("数据.tar.gz")
        self.touch("数据.tar(1).gz")
        self.touch("数据(1).tar.gz")
        found = Archiver._name_variants(self.tmp, "数据.tar.gz")
        self.assertEqual([p.name for p in found],
                         ["数据.tar.gz", "数据.tar(1).gz"])

    def test_空目录返回空列表(self) -> None:
        """测什么：什么都没有时不抛异常。"""
        self.assertEqual(Archiver._name_variants(self.tmp, "报告.docx"), [])


if __name__ == "__main__":          # pragma: no cover
    unittest.main()

# -*- coding: utf-8 -*-
"""app.summary.pipeline 的单元测试（分块 + JSON 降级解析 + 纯逻辑统计）。

测什么：build_blocks 的分块边界、_parse_json 的各级降级、_to_result 的
字段映射与过滤、compute_stats/Stats.to_rows 的纯代码统计、summarize 的
map-reduce 降级路径（用假客户端替换 DeepSeekClient，绝不联网）。
为什么：模型返回格式不可控，解析降级与分块边界是总结成功率的命脉。
所有消息时间都用固定 datetime，结果与当前时刻无关。
"""
from __future__ import annotations

import unittest
from datetime import datetime

from app.config import CHUNK_MAX_CHARS, CHUNK_MAX_MESSAGES
from app.models import Message, SummaryResult
from app.summary.pipeline import (Stats, _compact, _parse_json, _to_result,
                                  build_blocks, compute_stats, summarize)
from app.summary.prompts import MAP_PROMPT, REDUCE_PROMPT

# 固定基准时间：行格式里的时刻因此完全确定
BASE_TIME = datetime(2025, 1, 2, 9, 0, 0)
# 内容长度 137 时行长为 160 字符，12000 / 160 = 75 恰好整除，
# 用于验证"字符数达到上限时仍收入本块"（严格大于才换块）
EXACT_DIVISOR_CONTENT = "x" * 137


def make_message(index: int, content: str = "内容", name: str = "张三",
                 wxid: str = "wxid_a", msg_type: str = "文本",
                 file_name: str = "", when: datetime | None = None) -> Message:
    """构造一条测试用消息（时间可控，字段默认值明确）。"""
    return Message(chatroom_id="chatroom_1", sort_seq=index, local_id=index,
                   msg_type=msg_type, sender_wxid=wxid, sender_name=name,
                   content=content, create_time=when or BASE_TIME,
                   file_name=file_name)


class FakeClient:
    """替代 DeepSeekClient 的假客户端：按脚本回放，永不联网。

    脚本元素为字符串（正常返回）或异常实例（抛出）；供 summarize 的
    map-reduce 降级路径使用，并记录每次调用的 (system, user)。
    """

    def __init__(self, replies: list[object]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def chat(self, system: str, user: str, temperature: float = 0.3) -> str:
        self.calls.append((system, user))
        if not self.replies:
            raise AssertionError("测试脚本未提供更多回复")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return str(reply)


def long_messages(count: int = 2) -> list[Message]:
    """构造每条都超过字符上限的消息，保证一条一块。"""
    return [make_message(i, "y" * (CHUNK_MAX_CHARS + 1000))
            for i in range(1, count + 1)]


class BuildBlocksTest(unittest.TestCase):
    """分块：条数上限、字符上限与内容保真。"""

    def test_空列表返回空分块(self) -> None:
        """测什么：无消息不产生空块。为什么：空块会让模型空转。"""
        self.assertEqual(build_blocks([]), [])

    def test_单条消息一块(self) -> None:
        """测什么：少量消息合并为一块。"""
        blocks = build_blocks([make_message(1)])
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0], make_message(1).line())

    def test_按条数上限分块(self) -> None:
        """测什么：超过 CHUNK_MAX_MESSAGES 时换块。

        为什么：mock 出的短行不会先触发字符上限，正好验证条数上限。
        """
        messages = [make_message(i, "a") for i in range(1, 402)]
        self.assertLess(len(messages[0].line()) * CHUNK_MAX_MESSAGES,
                        CHUNK_MAX_CHARS)     # 前提：字符上限不先触发
        blocks = build_blocks(messages)
        self.assertEqual([len(b.split("\n")) for b in blocks],
                         [CHUNK_MAX_MESSAGES, 1])

    def test_恰好等于条数上限时仍是一块(self) -> None:
        """测什么：上限为闭区间（>= 才换块）。为什么：差一影响块数。"""
        messages = [make_message(i, "a") for i in range(1, 401)]
        blocks = build_blocks(messages)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(len(blocks[0].split("\n")), CHUNK_MAX_MESSAGES)

    def test_字符数恰好等于上限时仍收入本块(self) -> None:
        """测什么：字符判据是"加上本行后超过上限才换块"（严格大于）。

        为什么：若实现写成 >=，本用例的块会变成 74 行，请以本用例为准。
        """
        messages = [make_message(i, EXACT_DIVISOR_CONTENT)
                    for i in range(1, 151)]
        line_len = len(messages[0].line())
        self.assertEqual(line_len * 75, CHUNK_MAX_CHARS)   # 前提校验
        blocks = build_blocks(messages)
        self.assertEqual([len(b.split("\n")) for b in blocks], [75, 75])
        for block in blocks:
            self.assertEqual(sum(len(x) for x in block.split("\n")),
                             CHUNK_MAX_CHARS)

    def test_超长单条独占一块(self) -> None:
        """测什么：单条超过字符上限时不丢弃。为什么：长文消息不能丢。"""
        messages = long_messages(2)
        blocks = build_blocks(messages)
        self.assertEqual([len(b.split("\n")) for b in blocks], [1, 1])
        self.assertEqual(blocks[0], messages[0].line())

    def test_分块内容与原始行完全一致(self) -> None:
        """测什么：块拼接后等于所有行（不丢行、不改行）。

        为什么：分块是喂模型的唯一输入，丢行等于漏消息。
        """
        messages = [make_message(i, "z" * (i * 7)) for i in range(1, 61)]
        blocks = build_blocks(messages)
        self.assertEqual("\n".join(blocks),
                         "\n".join(m.line() for m in messages))


class ParseJsonTest(unittest.TestCase):
    """JSON 兜底解析：正常、围栏、夹杂说明、全角标点、尾逗号、失败。"""

    def test_正常_json(self) -> None:
        """测什么：标准 JSON 直接解析成功。"""
        self.assertEqual(_parse_json('{"a": 1}'), {"a": 1})

    def test_带_json_围栏(self) -> None:
        """测什么：```json 代码块围栏不影响解析。为什么：模型常见输出。"""
        text = '```json\n{"topics": [{"title": "t"}]}\n```'
        self.assertEqual(_parse_json(text),
                         {"topics": [{"title": "t"}]})

    def test_夹杂说明文字(self) -> None:
        """测什么：正文里夹一段 JSON 也能取出对象。"""
        text = "好的，以下是结果：\n```json\n{\"a\": 1}\n```\n希望有帮助"
        self.assertEqual(_parse_json(text), {"a": 1})

    def test_中文全角标点修复(self) -> None:
        """测什么：中文全角逗号/冒号被替换后解析成功。"""
        self.assertEqual(_parse_json('{"a"："中文"，"b": 2}'),
                         {"a": "中文", "b": 2})

    def test_尾逗号修复(self) -> None:
        """测什么：对象/数组尾逗号被正则去掉后解析成功。"""
        self.assertEqual(_parse_json('{"topics": [{"title": "x"},]}'),
                         {"topics": [{"title": "x"}]})
        self.assertEqual(_parse_json('{"a": 1,}'), {"a": 1})

    def test_没有花括号时返回_none(self) -> None:
        """测什么：纯文字输出无法解析。为什么：触发修复重试并计失败。"""
        self.assertIsNone(_parse_json("模型没有输出 JSON"))

    def test_花括号内不是_json_时返回_none(self) -> None:
        """测什么：有花括号但修不好，返回 None 而非抛异常。"""
        self.assertIsNone(_parse_json("{这不是 JSON}"))

    def test_两个_json_对象时返回_none(self) -> None:
        """测什么：贪婪正则跨两个对象导致整体不可解析（现状记录）。

        为什么：模型偶尔分段输出多个对象时会被判为解析失败，属已知
        限制，此处以用例固化，便于将来改成"逐个对象尝试"。
        """
        self.assertIsNone(_parse_json('{"a": 1} 说明 {"b": 2}'))

    def test_数组包裹对象时返回内层对象(self) -> None:
        """测什么：正则从首个 { 取到末个 }，数组外壳被丢掉。

        为什么：这是"宽松提取"的既有效果，调用方按 dict 使用没问题。
        """
        self.assertEqual(_parse_json('[{"a": 1}]'), {"a": 1})


class ToResultTest(unittest.TestCase):
    """模型 JSON → SummaryResult 的字段映射与脏数据过滤。"""

    def test_完整字段映射(self) -> None:
        """测什么：四模块字段逐个落到数据类，缺省补空串。

        为什么：字段名映射错误会让 Word 里整段空白。
        """
        data = {
            "topics": [{"title": "议题", "summary": "摘要"}],
            "action_items": [{"item": "干活", "owner": "张三",
                              "deadline": "周五"}],
            "notable_quotes": [{"sender": "李四", "quote": "金句",
                                "time": "10:00"}],
            "active_users": [{"name": "王五", "note": "话多"}],
        }
        result = _to_result(data)
        self.assertEqual(result.topics[0].title, "议题")
        self.assertEqual(result.topics[0].summary, "摘要")
        self.assertEqual(result.action_items[0].item, "干活")
        self.assertEqual(result.action_items[0].owner, "张三")
        self.assertEqual(result.action_items[0].deadline, "周五")
        self.assertEqual(result.notable_quotes[0].sender, "李四")
        self.assertEqual(result.notable_quotes[0].quote, "金句")
        self.assertEqual(result.notable_quotes[0].time, "10:00")
        self.assertEqual(result.active_users[0].name, "王五")
        self.assertEqual(result.active_users[0].note, "话多")
        self.assertEqual(result.active_users[0].msg_count, 0)
        self.assertFalse(result.partial_failed)

    def test_缺省值与_none_归一(self) -> None:
        """测什么：owner/deadline/sender/time/note 缺失或 None 时补空串。"""
        data = {"action_items": [{"item": "只给事项", "owner": None}],
                "notable_quotes": [{"quote": "只给金句", "sender": None}],
                "active_users": [{"name": "某人", "note": None}]}
        result = _to_result(data)
        self.assertEqual(result.action_items[0].owner, "")
        self.assertEqual(result.action_items[0].deadline, "")
        self.assertEqual(result.notable_quotes[0].sender, "")
        self.assertEqual(result.notable_quotes[0].time, "")
        self.assertEqual(result.active_users[0].note, "")

    def test_标题截断到_40_字(self) -> None:
        """测什么：过长标题被截断。为什么：Word 小标题排版需要。"""
        result = _to_result({"topics": [{"title": "很" * 50}]})
        self.assertEqual(len(result.topics[0].title), 40)

    def test_脏数据条目被过滤(self) -> None:
        """测什么：非 dict、缺关键字段、空串的条目被丢弃且不报错。

        为什么：模型偶尔返回 null 或字符串数组，不能让整篇总结失败。
        """
        data = {"topics": ["bad", {}, {"title": ""}, {"title": "留下"}],
                "action_items": [None, {}, {"item": ""}, {"item": "留下"}],
                "notable_quotes": ["x", {}, {"quote": ""}, {"quote": "留下"}],
                "active_users": [1, {}, {"name": ""}, {"name": "留下"}]}
        result = _to_result(data)
        self.assertEqual([t.title for t in result.topics], ["留下"])
        self.assertEqual([a.item for a in result.action_items], ["留下"])
        self.assertEqual([q.quote for q in result.notable_quotes], ["留下"])
        self.assertEqual([u.name for u in result.active_users], ["留下"])

    def test_缺失模块键与空字典(self) -> None:
        """测什么：完全没有四模块键时返回空结果。"""
        result = _to_result({})
        self.assertEqual(result.topics, [])
        self.assertEqual(result.action_items, [])
        self.assertEqual(result.notable_quotes, [])
        self.assertEqual(result.active_users, [])
        self.assertFalse(result.partial_failed)


class ComputeStatsTest(unittest.TestCase):
    """纯代码统计：总数、人数、文件数、类型分布、活跃榜、区间。"""

    def _sample(self) -> list[Message]:
        return [
            make_message(1, "早", name="张三", wxid="wxid_a"),
            make_message(2, "在", name="李四", wxid="wxid_b"),
            make_message(3, "系统提示", name="系统", wxid=""),
            make_message(4, "文件", name="张三", wxid="wxid_a",
                         msg_type="文件", file_name="a.pdf",
                         when=datetime(2025, 1, 2, 10, 0, 0)),
        ]

    def test_空消息返回全零(self) -> None:
        """测什么：无消息时统计为零且区间为空串。"""
        stats = compute_stats([], "群", "2025-01-02")
        self.assertEqual(stats.total, 0)
        self.assertEqual(stats.people, 0)
        self.assertEqual(stats.file_count, 0)
        self.assertEqual(stats.range_text, "")
        self.assertEqual(stats.top_users, [])
        self.assertEqual(stats.date, "2025-01-02")
        self.assertEqual(stats.group_name, "群")

    def test_各字段统计(self) -> None:
        """测什么：人数去重且跳过无 wxid、活跃榜跳过系统消息。"""
        stats = compute_stats(self._sample(), "群", "2025-01-02")
        self.assertEqual(stats.total, 4)
        self.assertEqual(stats.people, 2)
        self.assertEqual(stats.file_count, 1)
        self.assertEqual(stats.type_counter["文本"], 3)
        self.assertEqual(stats.type_counter["文件"], 1)
        self.assertEqual(stats.top_users, [("张三", 2), ("李四", 1)])
        self.assertEqual(stats.range_text, "09:00 - 10:00")

    def test_归档记录抬升文件数(self) -> None:
        """测什么：archived/missing 计入文件数，pending/skipped 不计。

        为什么：文件可能已被清理但记录仍在，清单要与实际归档对齐。
        """
        files = [{"status": "archived"}, {"status": "archived"},
                 {"status": "missing"}, {"status": "pending"},
                 {"status": "skipped"}, {}]
        stats = compute_stats(self._sample(), "群", "2025-01-02", files)
        self.assertEqual(stats.file_count, 3)

    def test_统计行内容与活跃榜上限(self) -> None:
        """测什么：to_rows 固定 5 行、活跃榜最多 5 人、无数据用 '-'。"""
        stats = compute_stats(self._sample(), "群", "2025-01-02")
        rows = stats.to_rows()
        self.assertEqual([r[0] for r in rows],
                         ["统计区间", "消息总数", "参与人数", "文件数量",
                          "最活跃"])
        self.assertEqual(rows[4][1], "张三(2条), 李四(1条)")

        stats.top_users = [(f"人{i}", i) for i in range(1, 8)]
        self.assertEqual(stats.to_rows()[4][1],
                         "人1(1条), 人2(2条), 人3(3条), 人4(4条), 人5(5条)")

        empty = compute_stats([], "群", "2025-01-02")
        self.assertEqual(empty.to_rows()[4][1], "-")
        self.assertEqual(empty.to_rows()[1][1], "0")


class SummarizeTest(unittest.TestCase):
    """map-reduce 主流程与降级路径（假客户端，不联网）。"""

    def test_无消息不调用客户端(self) -> None:
        """测什么：空输入直接返回空结果。为什么：省调用、省额度。"""
        client = FakeClient([])
        result = summarize(client, "群", [], Stats("2025-01-02", "群"))
        self.assertEqual(client.calls, [])
        self.assertFalse(result.partial_failed)

    def test_单块成功只调一次_map(self) -> None:
        """测什么：单块输入不做 reduce。为什么：避免多余的一次请求。"""
        client = FakeClient(['{"topics": [{"title": "议题"}]}'])
        result = summarize(client, "群", [make_message(1)],
                           Stats("2025-01-02", "群"))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0], MAP_PROMPT)
        self.assertEqual([t.title for t in result.topics], ["议题"])
        self.assertFalse(result.partial_failed)

    def test_单块非法_json_修复重试成功(self) -> None:
        """测什么：首次不是 JSON 时追加提醒再试一次即成功。"""
        client = FakeClient(["不是 JSON", '{"topics": [{"title": "重试"}]}'])
        result = summarize(client, "群", [make_message(1)],
                           Stats("2025-01-02", "群"))
        self.assertEqual(len(client.calls), 2)
        self.assertIn("请严格只输出 JSON", client.calls[1][1])
        self.assertEqual([t.title for t in result.topics], ["重试"])
        self.assertFalse(result.partial_failed)

    def test_单块始终非法_json_标记部分失败(self) -> None:
        """测什么：1 次原始 + 2 次修复共 3 次后放弃并标 partial_failed。"""
        client = FakeClient(["坏", "还是坏", "依然坏", "多余"])
        result = summarize(client, "群", [make_message(1)],
                           Stats("2025-01-02", "群"))
        self.assertEqual(len(client.calls), 3)
        self.assertTrue(result.partial_failed)
        self.assertEqual(result.topics, [])

    def test_map_调用抛异常时降级(self) -> None:
        """测什么：客户端异常被吞掉并标 partial_failed。为什么：不炸 UI。"""
        client = FakeClient([RuntimeError("接口挂了")])
        result = summarize(client, "群", [make_message(1)],
                           Stats("2025-01-02", "群"))
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(result.partial_failed)

    def test_多块成功走_reduce(self) -> None:
        """测什么：两块各自 map 后合并请求 reduce，用户提示块序号。"""
        client = FakeClient(['{"topics": [{"title": "块一"}]}',
                             '{"topics": [{"title": "块二"}]}',
                             '{"topics": [{"title": "合并"}]}'])
        result = summarize(client, "群", long_messages(2),
                           Stats("2025-01-02", "群"))
        self.assertEqual(len(client.calls), 3)
        self.assertIn("（第1/2块）", client.calls[0][1])
        self.assertIn("（第2/2块）", client.calls[1][1])
        self.assertEqual(client.calls[2][0], REDUCE_PROMPT)
        self.assertEqual([t.title for t in result.topics], ["合并"])
        self.assertFalse(result.partial_failed)

    def test_reduce_失败时拼接块结果(self) -> None:
        """测什么：reduce 返回不可解析内容时降级为块结果直接拼接。"""
        client = FakeClient(['{"topics": [{"title": "块一"}]}',
                             '{"topics": [{"title": "块二"}]}',
                             "reduce 输出不是 JSON"])
        result = summarize(client, "群", long_messages(2),
                           Stats("2025-01-02", "群"))
        self.assertEqual([t.title for t in result.topics], ["块一", "块二"])
        self.assertTrue(result.partial_failed)

    def test_reduce_抛异常时同样拼接(self) -> None:
        """测什么：reduce 请求异常也走同一条降级路径。"""
        client = FakeClient(['{"topics": [{"title": "块一"}]}',
                             '{"topics": [{"title": "块二"}]}',
                             RuntimeError("reduce 挂了")])
        result = summarize(client, "群", long_messages(2),
                           Stats("2025-01-02", "群"))
        self.assertEqual([t.title for t in result.topics], ["块一", "块二"])
        self.assertTrue(result.partial_failed)

    def test_多块中有一块失败时保留成功块(self) -> None:
        """测什么：两块里一块失败时仍交出另一块的结果。

        为什么：降级优先保内容；但注意此时 partial_failed 仍为 False，
        这是当前实现的一个疑似缺陷（见交付报告），本用例固化现状：
        成功块只有一块时走了提前返回分支，跳过了失败计数标记。
        """
        client = FakeClient([RuntimeError("第一块挂了"),
                             '{"topics": [{"title": "块二"}]}'])
        result = summarize(client, "群", long_messages(2),
                           Stats("2025-01-02", "群"))
        self.assertEqual([t.title for t in result.topics], ["块二"])
        self.assertFalse(result.partial_failed)   # 现状：疑似缺陷


class CompactTest(unittest.TestCase):
    """_compact：喂给 reduce 的中间结构。"""

    def test_输出四模块字段(self) -> None:
        """测什么：中间结构字段名与模型输入契约一致。"""
        result = _to_result({"topics": [{"title": "t", "summary": "s"}],
                             "action_items": [{"item": "i"}],
                             "notable_quotes": [{"quote": "q"}],
                             "active_users": [{"name": "n", "note": "x"}]})
        data = _compact(result)
        self.assertEqual(sorted(data), ["action_items", "active_users",
                                        "notable_quotes", "topics"])
        self.assertEqual(data["topics"], [{"title": "t", "summary": "s"}])
        self.assertEqual(data["active_users"], [{"name": "n", "note": "x"}])

    def test_空结果输出空数组(self) -> None:
        """测什么：空 SummaryResult 的中间结构是可序列化的空数组。"""
        data = _compact(SummaryResult())
        self.assertEqual(data["topics"], [])
        self.assertEqual(data["action_items"], [])


if __name__ == "__main__":          # pragma: no cover
    unittest.main()

# -*- coding: utf-8 -*-
"""app.state_store.StateStore 的 CRUD 单元测试（临时库文件）。

测什么：监控群、游标、summary_run、archived_file、error_log 的读写与
幂等语义。为什么：state.db 是增量轮询的"记忆"，游标倒退或归档记录重复
都会直接造成漏总结/重复总结。
每个测试用独立临时目录里的 state.db，结束后清理，绝不碰项目 data/。
"""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from app.state_store import StateStore
from tests.support import cleanup_dir, make_temp_dir

EXPECTED_TABLES = {"kv_config", "monitor_group", "summary_run",
                   "archived_file", "error_log"}


class StateStoreTestCase(unittest.TestCase):
    """公共装置：独立临时目录 + 独立 state.db。"""

    def setUp(self) -> None:
        self.tmp = make_temp_dir()
        self.addCleanup(cleanup_dir, self.tmp)

    def make_store(self) -> StateStore:
        """在临时目录的嵌套子目录里建库（顺带验证父目录自建）。"""
        return StateStore(db_path=self.tmp / "data" / "state.db")

    @property
    def db_path(self) -> Path:
        return self.tmp / "data" / "state.db"

    def raw_rows(self, sql: str, args: tuple = ()) -> list[tuple]:
        """绕过 StateStore 直接读库，用于校验内部落库细节。"""
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()


class SchemaTest(StateStoreTestCase):
    """建库与迁移。"""

    def test_建库自动创建父目录与五张表(self) -> None:
        """测什么：首次构造即建目录、建表，可重复构造（幂等）。"""
        store = self.make_store()
        self.assertTrue(self.db_path.is_file())
        names = {r[0] for r in self.raw_rows(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(EXPECTED_TABLES.issubset(names))
        del store
        StateStore(db_path=self.db_path)     # 二次构造不应报错
        self.assertTrue(self.db_path.is_file())


class GroupTest(StateStoreTestCase):
    """监控群与游标。"""

    def test_新增群与列表排序(self) -> None:
        """测什么：list_groups 按群名排序，enabled_only 只回启用的。

        排序期望值用 Python 排序算出（UTF-8 字节序与码点序一致，与
        SQLite 默认 BINARY 排序相同），避免手写字面顺序出错。
        """
        store = self.make_store()
        names = {"c1": "乙群", "c2": "甲群", "c3": "丙群"}
        store.upsert_group("c1", names["c1"])
        store.upsert_group("c2", names["c2"])
        store.upsert_group("c3", names["c3"], enabled=False)
        by_name = sorted(names, key=lambda cid: names[cid])
        self.assertEqual([g["chatroom_id"] for g in store.list_groups()],
                         by_name)
        self.assertEqual(
            [g["chatroom_id"] for g in store.list_groups(enabled_only=True)],
            [cid for cid in by_name if cid != "c3"])

    def test_新群游标为_negative_one_标记待初始化(self) -> None:
        """测什么：新插入群游标是 -1（待后台初始化为当天起点）。"""
        store = self.make_store()
        store.upsert_group("c1", "甲群", monitor_start_date="2025-01-01")
        group = store.get_group("c1")
        assert group is not None
        self.assertEqual(group["sort_seq_cursor"], -1)
        self.assertEqual(store.get_cursor("c1"), -1)
        self.assertEqual(group["monitor_start_date"], "2025-01-01")
        self.assertEqual(group["enabled"], 1)

    def test_重复写入只更新群名(self) -> None:
        """测什么：同 id 二次 upsert 只改群名，不动游标/启用日期/启用位。

        为什么：自动更名不能把用户已推进的游标或手动启用状态重置。
        """
        store = self.make_store()
        store.upsert_group("c1", "旧名", monitor_start_date="2025-01-01")
        store.advance_cursor("c1", 123)
        store.upsert_group("c1", "新名", enabled=False,
                           monitor_start_date="2099-12-31")
        group = store.get_group("c1")
        assert group is not None
        self.assertEqual(group["group_name"], "新名")
        self.assertEqual(group["sort_seq_cursor"], 123)
        self.assertEqual(group["monitor_start_date"], "2025-01-01")
        self.assertEqual(group["enabled"], 1)     # 启用位不被 upsert 覆盖

    def test_启用位由_set_group_enabled_单独控制(self) -> None:
        """测什么：set_group_enabled 生效且影响 enabled_only 过滤。"""
        store = self.make_store()
        store.upsert_group("c1", "甲群")
        store.set_group_enabled("c1", False)
        group = store.get_group("c1")
        assert group is not None
        self.assertEqual(group["enabled"], 0)
        self.assertEqual(store.list_groups(enabled_only=True), [])
        store.set_group_enabled("c1", True)
        self.assertEqual(len(store.list_groups(enabled_only=True)), 1)

    def test_移除群(self) -> None:
        """测什么：remove_group 后查不到且列表为空。"""
        store = self.make_store()
        store.upsert_group("c1", "甲群")
        store.remove_group("c1")
        self.assertIsNone(store.get_group("c1"))
        self.assertEqual(store.list_groups(), [])
        self.assertEqual(store.get_cursor("c1"), 0)   # 未登记群游标为 0

    def test_advance_cursor_只前进(self) -> None:
        """测什么：游标取 MAX，倒退写入被忽略。

        为什么：增量轮询靠游标单调递增，倒退会重复总结。
        """
        store = self.make_store()
        store.upsert_group("c1", "甲群")
        store.advance_cursor("c1", 100)
        self.assertEqual(store.get_cursor("c1"), 100)
        store.advance_cursor("c1", 50)
        self.assertEqual(store.get_cursor("c1"), 100)
        store.advance_cursor("c1", 101)
        self.assertEqual(store.get_cursor("c1"), 101)

    def test_advance_cursor_对未登记群静默无效果(self) -> None:
        """测什么：不存在的群不抛异常，也不会凭空建行。"""
        store = self.make_store()
        store.advance_cursor("nope", 999)
        self.assertIsNone(store.get_group("nope"))
        self.assertEqual(store.list_groups(), [])

    def test_set_cursor_允许倒退(self) -> None:
        """测什么：手动重置场景允许游标回退。"""
        store = self.make_store()
        store.upsert_group("c1", "甲群")
        store.advance_cursor("c1", 200)
        store.set_cursor("c1", 10)
        self.assertEqual(store.get_cursor("c1"), 10)

    def test_mark_summaried_记录时间与文档路径(self) -> None:
        """测什么：last_summary_at 非空、last_docx_path 忠实写入。"""
        store = self.make_store()
        store.upsert_group("c1", "甲群")
        store.mark_summaried("c1", "输出/甲群/2025-01-02.docx")
        group = store.get_group("c1")
        assert group is not None
        self.assertEqual(group["last_docx_path"], "输出/甲群/2025-01-02.docx")
        self.assertTrue(group["last_summary_at"])


class SummaryRunTest(StateStoreTestCase):
    """总结运行记录。"""

    def test_开始与结束一次运行(self) -> None:
        """测什么：id 自增，finish 回填区间/条数/状态/文档路径。"""
        store = self.make_store()
        store.upsert_group("c1", "甲群")
        first = store.start_summary_run("c1", "2025-01-02", 100)
        second = store.start_summary_run("c1", "2025-01-02", 200)
        self.assertGreater(second, first)
        store.finish_summary_run(first, 150, 50, "done", "a.docx")
        row = store.last_finished_run("c1")
        assert row is not None
        self.assertEqual(row["id"], first)
        self.assertEqual(row["seq_start"], 100)
        self.assertEqual(row["seq_end"], 150)
        self.assertEqual(row["msg_count"], 50)
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["docx_path"], "a.docx")
        self.assertTrue(row["created_at"])

    def test_last_finished_run_只认_done_的最后一条(self) -> None:
        """测什么：running/failed 不算完成，取 id 最大的 done。"""
        store = self.make_store()
        run1 = store.start_summary_run("c1", "2025-01-02", 1)
        run2 = store.start_summary_run("c1", "2025-01-02", 2)
        run3 = store.start_summary_run("c1", "2025-01-02", 3)
        store.finish_summary_run(run1, 10, 10, "done", "a.docx")
        store.finish_summary_run(run2, 20, 10, "failed", "")
        store.finish_summary_run(run3, 30, 10, "done", "c.docx")
        row = store.last_finished_run("c1")
        assert row is not None
        self.assertEqual(row["id"], run3)
        self.assertEqual(row["docx_path"], "c.docx")

    def test_没有完成的运行时返回_none(self) -> None:
        """测什么：只有 running 记录时返回 None；别的群互不影响。"""
        store = self.make_store()
        store.start_summary_run("c1", "2025-01-02", 1)
        self.assertIsNone(store.last_finished_run("c1"))
        self.assertIsNone(store.last_finished_run("c2"))


class ArchivedFileTest(StateStoreTestCase):
    """归档文件登记（幂等性）。"""

    def test_同_svr_id_二次写入不新增行(self) -> None:
        """测什么：svr_id 命中时更新原行（含状态/路径/大小）。"""
        store = self.make_store()
        first = store.upsert_archived_file("c1", "2025-01-02", "a.pdf", "p1",
                                          10, "svr_1")
        second = store.upsert_archived_file("c1", "2025-01-02", "a.pdf", "p2",
                                           10, "svr_1", status="missing")
        self.assertEqual(first, second)
        rows = store.list_archived_files("c1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["saved_path"], "p2")
        self.assertEqual(rows[0]["status"], "missing")
        self.assertTrue(rows[0]["archived_at"])

    def test_不同_svr_id_同名同大小只留一行_现状记录(self) -> None:
        """测什么：svr_id 不同但同名同日期同大小时的行数（现状固化）。

        为什么：文档说"svr_id 非空时按 (chatroom_id, svr_id)"判重，但
        实现里 svr_id 查不到会继续退化为四元组匹配，于是两个不同
        svr_id 的同名同大小文件被合并成一行——该行还保留第一个 svr_id
        却指向第二个 saved_path，两个文件谁都没被正确登记（疑似缺陷，
        见交付报告）。
        """
        store = self.make_store()
        first = store.upsert_archived_file("c1", "2025-01-02", "a.pdf", "p1",
                                           10, "svr_1")
        second = store.upsert_archived_file("c1", "2025-01-02", "a.pdf", "p2",
                                            10, "svr_2")
        self.assertEqual(first, second)              # 现状：复用同一行
        rows = store.list_archived_files("c1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["svr_id"], "svr_1")  # 更新不改 svr_id
        self.assertEqual(rows[0]["saved_path"], "p2")

    def test_不同_svr_id_且大小不同时各占一行(self) -> None:
        """测什么：svr_id 与大小都不同时正常各占一行（四元组不命中）。"""
        store = self.make_store()
        first = store.upsert_archived_file("c1", "2025-01-02", "a.pdf", "p1",
                                           10, "svr_1")
        second = store.upsert_archived_file("c1", "2025-01-02", "a.pdf", "p2",
                                            77, "svr_2")
        self.assertNotEqual(first, second)
        rows = store.list_archived_files("c1")
        self.assertEqual([r["svr_id"] for r in rows], ["svr_1", "svr_2"])

    def test_无_svr_id_时按四元组幂等(self) -> None:
        """测什么：无 svr_id 时用 (群,日期,原名,大小) 去重。"""
        store = self.make_store()
        first = store.upsert_archived_file("c1", "2025-01-02", "a.pdf", "p1",
                                           10, "")
        second = store.upsert_archived_file("c1", "2025-01-02", "a.pdf", "p2",
                                            10, "")
        self.assertEqual(first, second)
        self.assertEqual(len(store.list_archived_files("c1")), 1)

    def test_同名不同大小视为两个文件(self) -> None:
        """测什么：同名不同字节数各占一行。为什么：微信重名变体。"""
        store = self.make_store()
        first = store.upsert_archived_file("c1", "2025-01-02", "a.pdf", "p1",
                                           10, "")
        second = store.upsert_archived_file("c1", "2025-01-02", "a.pdf", "p2",
                                            99, "")
        self.assertNotEqual(first, second)
        sizes = sorted(r["size"] for r in store.list_archived_files("c1"))
        self.assertEqual(sizes, [10, 99])

    def test_列表过滤与排序(self) -> None:
        """测什么：按群/日期过滤，按 id 升序，条件为空则全量。"""
        store = self.make_store()
        store.upsert_archived_file("c1", "2025-01-02", "a.pdf", "p1", 1, "s1")
        store.upsert_archived_file("c1", "2025-01-03", "b.pdf", "p2", 2, "s2")
        store.upsert_archived_file("c2", "2025-01-02", "c.pdf", "p3", 3, "s3")
        self.assertEqual([r["orig_name"]
                          for r in store.list_archived_files("c1")],
                         ["a.pdf", "b.pdf"])
        self.assertEqual([r["orig_name"]
                          for r in store.list_archived_files("c1",
                                                             "2025-01-03")],
                         ["b.pdf"])
        self.assertEqual(store.list_archived_files("c1", "2000-01-01"), [])
        self.assertEqual(len(store.list_archived_files()), 3)
        self.assertEqual([r["orig_name"]
                          for r in store.list_archived_files(None,
                                                             "2025-01-02")],
                         ["a.pdf", "c.pdf"])


class KvAndErrorLogTest(StateStoreTestCase):
    """kv 配置与错误日志。"""

    def test_kv_读写与默认值(self) -> None:
        """测什么：get_kv 缺省返回 default，重复 set 覆盖旧值。"""
        store = self.make_store()
        self.assertIsNone(store.get_kv("missing"))
        self.assertEqual(store.get_kv("missing", "d"), "d")
        store.set_kv("k", "v1")
        store.set_kv("k", "v2")
        self.assertEqual(store.get_kv("k"), "v2")
        self.assertEqual(self.raw_rows("SELECT COUNT(*) FROM kv_config"),
                         [(1,)])

    def test_log_error_落库并截断超长消息(self) -> None:
        """测什么：level/module/message 落库，message 截断到 2000 字符。"""
        store = self.make_store()
        store.log_error("ERROR", "app.core.archiver", "m" * 2500)
        store.log_error("WARN", "app.core.archiver", "短消息")
        rows = self.raw_rows(
            "SELECT level, module, message, ts FROM error_log ORDER BY id")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "ERROR")
        self.assertEqual(rows[0][1], "app.core.archiver")
        self.assertEqual(len(rows[0][2]), 2000)
        self.assertTrue(rows[0][3])
        self.assertEqual(rows[1][2], "短消息")


if __name__ == "__main__":          # pragma: no cover
    unittest.main()

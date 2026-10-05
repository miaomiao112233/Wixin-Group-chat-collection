# -*- coding: utf-8 -*-
"""运行自检 / 缓存清理 / 诊断包测试。

缓存相关用临时目录（`prune_dbcache(root=...)` 支持注入），绝不碰项目里
真实的 dbcache；诊断包写到临时目录。
"""
from __future__ import annotations

import os
import sys
import time
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support import cleanup_dir, make_temp_dir      # noqa: E402

from app.core import maintenance                          # noqa: E402


def _touch(path: Path, size: int, age_days: float) -> None:
    """造一个指定大小、指定"多少天前修改"的文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_days * 86400.0
    os.utime(path, (stamp, stamp))


class FormatSizeTest(unittest.TestCase):
    def test_各单位(self):
        self.assertEqual(maintenance.format_size(0), "0 B")
        self.assertEqual(maintenance.format_size(999), "999 B")
        self.assertEqual(maintenance.format_size(1024), "1.0 KB")
        self.assertEqual(maintenance.format_size(1536), "1.5 KB")
        self.assertEqual(maintenance.format_size(5 * 1024 ** 2), "5.0 MB")


class ScanDbcacheTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(make_temp_dir())

    def tearDown(self):
        cleanup_dir(self.root)

    def test_统计总量文件数最旧时间与分角色(self):
        _touch(self.root / "monitor" / "a.db", 100, age_days=1)
        _touch(self.root / "monitor" / "b.db", 200, age_days=9)
        _touch(self.root / "summary" / "c.db", 50, age_days=3)
        info = maintenance.scan_dbcache(self.root)
        self.assertEqual(info["files"], 3)
        self.assertEqual(info["total"], 350)
        self.assertEqual(info["per_role"]["monitor"], 300)
        self.assertEqual(info["per_role"]["summary"], 50)
        age_days = (time.time() - info["oldest"]) / 86400
        self.assertAlmostEqual(age_days, 9, delta=0.2)

    def test_目录不存在时返回空(self):
        info = maintenance.scan_dbcache(self.root / "not-there")
        self.assertEqual((info["files"], info["total"], info["oldest"]),
                         (0, 0, 0.0))


class PruneDbcacheTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(make_temp_dir())

    def tearDown(self):
        cleanup_dir(self.root)

    def test_按天数删除过期文件保留新文件(self):
        old = self.root / "monitor" / "old.db"
        new = self.root / "monitor" / "new.db"
        _touch(old, 1000, age_days=10)
        _touch(new, 1000, age_days=1)
        freed, removed = maintenance.prune_dbcache(keep_days=7, max_mb=0,
                                                  root=self.root)
        self.assertEqual((freed, removed), (1000, 1))
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_keep_days为0时不做天数清理(self):
        _touch(self.root / "monitor" / "old.db", 500, age_days=999)
        freed, removed = maintenance.prune_dbcache(keep_days=0, max_mb=0,
                                                   root=self.root)
        self.assertEqual((freed, removed), (0, 0))
        self.assertTrue((self.root / "monitor" / "old.db").exists())

    def test_超容量时从最旧开始删到限额内(self):
        # 3 个文件各 1MB，上限 2MB -> 必须删掉最旧的那个
        for name, age in (("oldest.db", 30), ("mid.db", 20), ("newest.db", 10)):
            _touch(self.root / "monitor" / name, 1024 * 1024, age_days=age)
        freed, removed = maintenance.prune_dbcache(keep_days=0, max_mb=2,
                                                   root=self.root)
        self.assertEqual(removed, 1)
        self.assertEqual(freed, 1024 * 1024)
        self.assertFalse((self.root / "monitor" / "oldest.db").exists())
        self.assertTrue((self.root / "monitor" / "mid.db").exists())
        self.assertTrue((self.root / "monitor" / "newest.db").exists())

    def test_保存目录且不误删非缓存文件(self):
        sub = self.root / "ui"
        _touch(sub / "old.db", 10, age_days=30)
        freed, removed = maintenance.prune_dbcache(keep_days=1, max_mb=0,
                                                   root=self.root)
        self.assertEqual(removed, 1)
        self.assertTrue(sub.is_dir())          # 目录本身保留

    def test_空目录安全(self):
        self.assertEqual(
            maintenance.prune_dbcache(keep_days=1, max_mb=1, root=self.root),
            (0, 0))


class DirToolsTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(make_temp_dir())

    def tearDown(self):
        cleanup_dir(self.root)

    def test_可写目录判定(self):
        self.assertTrue(maintenance.dir_writable(self.root))

    def test_目录体积统计(self):
        _touch(self.root / "a" / "1.bin", 300, age_days=0)
        _touch(self.root / "b" / "2.bin", 700, age_days=0)
        self.assertEqual(maintenance.dir_size(self.root), 1000)

    def test_磁盘剩余空间为正(self):
        self.assertGreater(maintenance.disk_free(self.root), 0)


class ChecksTest(unittest.TestCase):
    def test_自检项结构完整(self):
        checks = maintenance.collect_checks()
        self.assertGreaterEqual(len(checks), 8)
        for item in checks:
            self.assertIn(item["level"], ("ok", "warn", "error", "info"))
            self.assertTrue(item["name"])
            self.assertIsInstance(item["detail"], str)
        names = [c["name"] for c in checks]
        for expect in ("微信进程", "state.db", "AI 配置", "解密缓存"):
            self.assertIn(expect, names)

    def test_占用说明包含策略(self):
        hint = maintenance.build_size_hint()
        self.assertIn("解密缓存", hint)
        self.assertIn("保留", hint)


class ExportDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(make_temp_dir())

    def tearDown(self):
        cleanup_dir(self.root)

    def test_导出内容且不含微信密钥(self):
        dest = self.root / "diag.zip"
        out = maintenance.export_diagnostics(dest)
        self.assertTrue(Path(out).exists())
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            self.assertIn("system.txt", names)
            self.assertIn("config.redacted.json", names)
            self.assertIn("errors.csv", names)
            # 绝不能带上微信解密密钥
            self.assertNotIn("wechat_keys.json", names)
            self.assertFalse([n for n in names if "wechat_keys" in n])
            system = zf.read("system.txt").decode("utf-8")
            self.assertIn("程序版本", system)
            self.assertIn("自检结果", system)
            self.assertIn("不含 wechat_keys.json", system)
            cfg = zf.read("config.redacted.json").decode("utf-8")
            self.assertNotIn("sk-", cfg)

    def test_目标目录不存在时自动创建(self):
        dest = self.root / "sub" / "deep" / "diag.zip"
        maintenance.export_diagnostics(dest)
        self.assertTrue(dest.exists())


if __name__ == "__main__":
    unittest.main()

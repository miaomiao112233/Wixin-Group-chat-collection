# -*- coding: utf-8 -*-
r"""界面预览脚本：不开微信、不连数据库，把各个窗口渲染成 PNG 便于看效果。

    .\.venv\Scripts\python.exe scripts\ui_preview.py

产物在 输出\ui_preview\ 下：主窗口（普通/最大化）、设置、添加群、更新、
消息框。默认用 offscreen 后端（不弹窗、不抢焦点）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# offscreen 后端默认没有字体目录，指到系统字体，中文才不会渲染成方块
os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR",
                                                                r"C:\Windows")) / "Fonts"))

from PySide6.QtCore import QObject, Signal                # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap       # noqa: E402
from PySide6.QtWidgets import QApplication                # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "输出" / "ui_preview"

_FAKE_GROUPS = [
    {"chatroom_id": "111@chatroom", "group_name": "2024级计算机1班通知群",
     "enabled": 1, "sort_seq_cursor": 100, "monitor_start_date": "2026-01-01",
     "last_summary_path": "输出/2024级计算机1班通知群/2026-01-01.docx"},
    {"chatroom_id": "222@chatroom", "group_name": "项目组·周三例会",
     "enabled": 1, "sort_seq_cursor": 100},
    {"chatroom_id": "333@chatroom", "group_name": "家长交流群（含长群名测试·"
                                                 "二年级三班家委会通知发布专用）",
     "enabled": 0, "sort_seq_cursor": 100},
    {"chatroom_id": "444@chatroom", "group_name": "羽毛球约球群",
     "enabled": 1, "sort_seq_cursor": 100},
    {"chatroom_id": "555@chatroom", "group_name": "工作汇报群",
     "enabled": 1, "sort_seq_cursor": -1},
]

_FAKE_STATUS = {
    "111@chatroom": {"today_count": 128, "summaried": True,
                     "last_msg": "班长: 明天早上八点在教学楼门口集合，记得带学生证"},
    "222@chatroom": {"today_count": 42, "summaried": False,
                     "last_msg": "李工: 这版原型图我改完了，大家看下还有没有问题"},
    "333@chatroom": {"today_count": 7, "summaried": False,
                     "last_msg": "王老师: 各位家长好，本周五下午三点召开家长会"},
    "444@chatroom": {"today_count": 0, "summaried": False, "last_msg": ""},
    "555@chatroom": {"today_count": 310, "summaried": False,
                     "last_msg": "张总: 周报记得今天下班前发我"},
}

_FAKE_RUNS = [
    {"id": 9, "chatroom_id": "111@chatroom", "date": "2026-01-06",
     "status": "done", "msg_count": 128, "created_at": "2026-01-06 21:15:02",
     "docx_path": "输出/2024级计算机1班通知群/2026-01-06/群总结.docx"},
    {"id": 8, "chatroom_id": "222@chatroom", "date": "2026-01-06",
     "status": "failed", "msg_count": 42, "created_at": "2026-01-06 21:15:01",
     "docx_path": ""},
    {"id": 7, "chatroom_id": "555@chatroom", "date": "2026-01-06",
     "status": "running", "msg_count": 310, "created_at": "2026-01-06 21:15:00",
     "docx_path": ""},
    {"id": 6, "chatroom_id": "111@chatroom", "date": "2026-01-05",
     "status": "done", "msg_count": 96, "created_at": "2026-01-05 20:40:11",
     "docx_path": "输出/2024级计算机1班通知群/2026-01-05/群总结.docx"},
    {"id": 5, "chatroom_id": "444@chatroom", "date": "2026-01-05",
     "status": "done", "msg_count": 0, "created_at": "2026-01-05 20:40:10",
     "docx_path": "输出/羽毛球约球群/2026-01-05/群总结.docx"},
]

_FAKE_FILES = [
    {"id": 3, "chatroom_id": "111@chatroom", "date": "2026-01-06",
     "orig_name": "寒假作业安排.pdf", "size": 486_233,
     "status": "archived", "saved_path": "输出/2024级计算机1班通知群/2026-01-06/寒假作业安排.pdf",
     "archived_at": "2026-01-06 21:16:00"},
    {"id": 2, "chatroom_id": "111@chatroom", "date": "2026-01-06",
     "orig_name": "期末考试范围.xlsx", "size": 21_504,
     "status": "missing", "saved_path": "",
     "archived_at": "2026-01-06 21:16:01"},
    {"id": 1, "chatroom_id": "222@chatroom", "date": "2026-01-05",
     "orig_name": "周会纪要.docx", "size": 15_872,
     "status": "archived", "saved_path": "输出/项目组·周三例会/2026-01-05/周会纪要.docx",
     "archived_at": "2026-01-05 20:41:00"},
]

_FAKE_ERRORS = [
    {"ts": "2026-01-06 21:15:01", "level": "ERROR", "module": "summary",
     "message": "总结 项目组·周三例会 失败: 429 Too Many Requests（已重试 3 次）"},
    {"ts": "2026-01-06 09:02:13", "level": "ERROR", "module": "connect",
     "message": "未检测到微信运行：请先登录微信 PC 版（4.0 以上版本）"},
]


# ---------- 替身：不碰微信数据库 / 不真起线程 / 不要托盘 ----------
class _FakeStore:
    def list_groups(self, enabled_only: bool = False):
        return [g for g in _FAKE_GROUPS if g["enabled"] or not enabled_only]

    def get_group(self, chatroom_id: str):
        for g in _FAKE_GROUPS:
            if g["chatroom_id"] == chatroom_id:
                return g
        return None

    def set_group_enabled(self, *a, **kw):
        pass

    def remove_group(self, *a, **kw):
        pass

    def upsert_group(self, *a, **kw):
        pass

    def mark_summaried(self, *a, **kw):
        pass

    def get_kv(self, key, default=None):
        return default

    def set_kv(self, *a, **kw):
        pass

    # ---- 历史面板用到的查询 ----
    def list_summary_runs(self, limit=300, chatroom_id=None, status=None):
        rows = [dict(r) for r in _FAKE_RUNS]
        if chatroom_id:
            rows = [r for r in rows if r["chatroom_id"] == chatroom_id]
        if status:
            rows = [r for r in rows if r["status"] == status]
        return rows[:limit]

    def today_docx(self, chatroom_id, date):
        for r in _FAKE_RUNS:
            if (r["chatroom_id"] == chatroom_id and r["date"] == date
                    and r["status"] == "done" and r.get("docx_path")):
                return r["docx_path"]
        return None

    def list_archived_files(self, chatroom_id=None, date=None):
        rows = [dict(r) for r in _FAKE_FILES]
        if chatroom_id:
            rows = [r for r in rows if r["chatroom_id"] == chatroom_id]
        if date:
            rows = [r for r in rows if r["date"] == date]
        return rows

    def list_errors(self, limit=300):
        return [dict(r) for r in _FAKE_ERRORS][:limit]

    def clear_errors(self):
        return 0


class _StubMonitor(QObject):
    log = Signal(str)
    status = Signal(dict)
    next_summary_at = Signal(str)
    ask_summary = Signal(str, str, str)
    connect_failed = Signal(str)
    connected = Signal()
    request_poll = Signal()
    request_reconnect = Signal()
    reschedule_requested = Signal()
    keyword_alert = Signal(str, str, str, str)

    def __init__(self, store):
        super().__init__()

    def start_work(self):
        pass

    def stop(self):
        pass


class _StubSummary(QObject):
    log = Signal(str)
    done = Signal(str, str)
    failed = Signal(str, str)
    ai_config_set = Signal(str, str, str, bool)

    def __init__(self, store):
        super().__init__()

    def start_work(self):
        pass

    def stop(self):
        pass

    def submit(self, *a, **kw):
        pass


class _StubTray(QObject):
    def __init__(self, *a, **kw):
        super().__init__()

    def show(self):
        pass

    def hide(self):
        pass

    def showMessage(self, *a, **kw):
        pass

    def set_state(self, *a, **kw):
        pass

    def state(self):
        return "monitor"

    def set_tooltip(self, *a, **kw):
        pass


def _install_stubs() -> None:
    import app.core.maintenance as maintenance_mod
    import app.state_store as state_store_mod
    import app.workers.monitor_worker as monitor_mod
    import app.workers.summary_worker as summary_mod
    import app.ui.frameless as frameless_mod
    import app.ui.tray as tray_mod
    state_store_mod.StateStore = _FakeStore
    monitor_mod.MonitorWorker = _StubMonitor
    summary_mod.SummaryWorker = _StubSummary
    tray_mod.TrayIcon = _StubTray
    # offscreen 平台没有真实 HWND，原生边框改装必然失败；这里直接禁用，
    # 强制走透明自绘模式，截图里才能看到圆角与投影
    frameless_mod._win_api = lambda: None
    # 预览脚本绝不能动真实目录：缓存清理换掉（自检只读，保留）
    maintenance_mod.prune_dbcache = lambda *a, **kw: (0, 0)


def _shot(widget, name: str, background: str = "#C9D3D0") -> Path:
    """渲染成 PNG（透明区铺一层底色，圆角/投影看得清）。"""
    widget.grab()                       # 先渲染一次，确保布局生效
    pm = widget.grab()
    canvas = QPixmap(pm.size())
    canvas.fill(QColor(background))
    painter = QPainter(canvas)
    painter.drawPixmap(0, 0, pm)
    painter.end()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.png"
    canvas.save(str(path))
    print(f"[shot] {path}  {pm.width()}x{pm.height()}")
    return path


def main() -> int:
    _install_stubs()
    from app.core.updater import ReleaseInfo
    from app.ui.group_dialog import GroupDialog
    from app.ui.history_dialog import RecordsDialog
    from app.ui.main_window import MainWindow
    from app.ui.msgbox import MessageDialog
    from app.ui.settings_dialog import SettingsDialog
    from app.ui.update_dialog import UpdateDialog

    app = QApplication(sys.argv)
    app.setApplicationName("WxSumPreview")

    win = MainWindow()
    win.resize_content(1020, 680)
    win.show()
    app.processEvents()
    win._on_status(_FAKE_STATUS)
    app.processEvents()
    _shot(win, "01_main")

    win.showMaximized()
    app.processEvents()
    _shot(win, "02_main_maximized")
    win.showNormal()
    app.processEvents()

    geo = win.geometry()
    win.move(max(0, geo.x() - 40), max(0, geo.y() - 40))
    win.resize_content(700, 640)          # 窄窗口：列数应自适应变少
    app.processEvents()
    _shot(win, "03_main_narrow")
    win.resize_content(1020, 680)
    app.processEvents()

    s = SettingsDialog(win)
    s.show()
    app.processEvents()
    _shot(s, "04_settings")
    s.sec_alert.set_expanded(True, animate=False)     # 展开一组看高度是否跟随
    s.sec_cache.set_expanded(True, animate=False)
    app.processEvents()
    _shot(s, "04b_settings_expanded")
    s.close()

    g = GroupDialog(
        [{"username": f"{i}@chatroom", "name": n} for i, n in
         enumerate(["2024级计算机1班通知群", "项目组·周三例会", "羽毛球约球群"])],
        set(), win)
    g.show()
    app.processEvents()
    _shot(g, "05_group")
    g.close()

    rel = ReleaseInfo(version="1.3.0", tag="v1.3.0",
                      notes="## 更新内容\n- 窗口改为 QQ / 微信 风格的无边框界面\n"
                            "- 自绘标题栏，去掉左上角系统图标\n- 修复若干问题",
                      url="https://example.com/WxSum.zip", size=12_300_000,
                      prerelease=False)
    u = UpdateDialog(rel, win)
    u.show()
    app.processEvents()
    _shot(u, "06_update")

    for kind, name in (("info", "07_msg_info"), ("warning", "08_msg_warning"),
                       ("question", "09_msg_question")):
        m = MessageDialog(win, "提示" if kind == "info" else "注意",
                          "设置已保存，AI 服务配置已更新。" if kind == "info"
                          else "读取群列表失败（微信需已登录）\n"
                               "ConnectionError: 未找到正在运行的 Weixin.exe 进程",
                          kind, [("取消", False), ("确定", True)])
        m.show()
        app.processEvents()
        _shot(m, name)
        m.close()

    # 记录与诊断窗口（4 个标签页各来一张）
    r = RecordsDialog(win._store, win)
    r.resize_content(900, 620)
    r.show()
    app.processEvents()
    _shot(r, "11_records_runs")
    for idx, name in ((1, "12_records_files"), (2, "13_records_logs"),
                      (3, "14_records_checks")):
        r.tabs.setCurrentIndex(idx)
        app.processEvents()
        _shot(r, name)
    r.close()

    # 透明度自检：投影留白是否真的透明（有投影时该点应带一点暗色）
    px = _shot(win, "10_main_final").exists()
    print("[check] 截图已生成:", px)
    win._shutdown()                     # 收掉替身线程，避免退出告警
    return 0


if __name__ == "__main__":
    sys.exit(main())

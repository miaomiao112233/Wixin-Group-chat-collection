# -*- coding: utf-8 -*-
"""「记录与诊断」窗口：总结记录 / 归档文件 / 日志与错误 / 自检与维护。

- 总结记录：state.db 的 summary_run，可打开 Word、按同样的日期重新生成
  （交给主窗口的 SummaryWorker）；失败的记录能直接看到错误
- 归档文件：archived_file 表，可打开文件/所在文件夹、导出 CSV
- 日志与错误：error_log 表 + logs/app.log 实时尾巴，便于自助排查
- 自检与维护：运行自检、导出诊断包、清理解密缓存
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QPlainTextEdit, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from app.config import DATA_DIR, LOGS_DIR, OUTPUT_DIR
from app.core import maintenance
from app.ui import msgbox
from app.ui.frameless import FramelessDialog
from app.ui.shell import open_path as _open, reveal_path as _reveal

_RUN_STATUS = {"done": "已完成", "failed": "失败", "running": "进行中"}
_FILE_STATUS = {"archived": "已归档", "pending": "待下载",
                "missing": "未下载", "skipped": "未归档类型"}
_LEVEL = {"ok": ("正常", "#07C160"), "warn": ("注意", "#E6A23C"),
          "error": ("异常", "#E0524A"), "info": ("信息", "#8A9491")}

_QSS = """
QLabel { color: #2B3A35; font-size: 13px; }
QLabel#hint { color: #8A9491; font-size: 12px; }
QTabWidget::pane { border: 1px solid #E6EAE8; border-radius: 10px;
                   background: #FFFFFF; top: -1px; }
QTabBar::tab {
    background: transparent; color: #5B6663; padding: 7px 16px;
    font-size: 13px; border: none; margin-right: 2px;
}
QTabBar::tab:selected { color: #07C160; font-weight: 600;
                        border-bottom: 2px solid #07C160; }
QTabBar::tab:hover { color: #07C160; }
QComboBox {
    border: 1px solid #DCE2E0; border-radius: 8px; padding: 5px 10px;
    font-size: 13px; background: #FFFFFF; color: #2B3A35;
}
QComboBox::drop-down { border: none; width: 20px; }
QPushButton {
    background: #F1F5F3; color: #2B3A35; border: none;
    border-radius: 8px; padding: 6px 14px; font-size: 12px;
}
QPushButton:hover { background: #E2EAE6; }
QPushButton#accent { background: #07C160; color: #FFFFFF; font-weight: 600; }
QPushButton#accent:hover { background: #06AD56; }
QTableWidget {
    background: #FFFFFF; border: 1px solid #E6EAE8; border-radius: 10px;
    gridline-color: #EEF1F0; font-size: 12px; color: #2B3A35;
}
QHeaderView::section {
    background: #F7F9F8; color: #5B6663; border: none;
    border-bottom: 1px solid #E6EAE8; padding: 6px 8px; font-size: 12px;
}
QTableWidget::item:selected { background: #E4F6EC; color: #1F2D2A; }
QPlainTextEdit {
    background: #FFFFFF; border: 1px solid #E6EAE8; border-radius: 10px;
    color: #4A5551; font-size: 12px;
}
QCheckBox { color: #5B6663; font-size: 12px; spacing: 6px; }
"""


def _read_tail(path: Path, max_bytes: int = 200_000) -> str:
    """读文件尾部若干字节（日志可能很大，不整份读）。"""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _make_table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    return t


def _fit_columns(table: QTableWidget, max_width: int = 240) -> None:
    """按内容自适应列宽，但给每列设上限——否则长路径会把窗口撑爆。"""
    table.resizeColumnsToContents()
    for c in range(table.columnCount() - 1):
        if table.columnWidth(c) > max_width:
            table.setColumnWidth(c, max_width)
    table.horizontalHeader().setStretchLastSection(True)


def _cell(text: str, data=None) -> QTableWidgetItem:
    """带完整文本 tooltip 的单元格（列宽受限时仍能看全内容）。"""
    item = QTableWidgetItem(str(text))
    if data is not None:
        item.setData(Qt.UserRole, data)
    item.setToolTip(str(text))
    return item


class RecordsDialog(FramelessDialog):
    regenerate = Signal(str, str, str)      # chatroom, group_name, date

    def __init__(self, store, parent=None):
        super().__init__(parent, title="记录与诊断", resizable=True)
        self.resize_content(900, 620)
        self.setStyleSheet(_QSS)
        self._store = store
        self._group_names: dict[str, str] = {}
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(2000)
        self._log_timer.timeout.connect(self._tail_log)
        self._build_ui()
        self.reload()

    # ---------- 界面 ----------
    def _build_ui(self):
        root = QVBoxLayout()
        root.setContentsMargins(18, 4, 18, 16)
        root.setSpacing(10)
        self.body_layout.addLayout(root)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._tab_runs(), "总结记录")
        self.tabs.addTab(self._tab_files(), "归档文件")
        self.tabs.addTab(self._tab_logs(), "日志与错误")
        self.tabs.addTab(self._tab_checks(), "自检与维护")

    # ---- 总结记录 ----
    def _tab_runs(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.cmb_run_group = QComboBox()
        self.cmb_run_group.setMinimumWidth(180)
        self.cmb_run_status = QComboBox()
        self.cmb_run_status.addItem("全部状态", None)
        for key, label in _RUN_STATUS.items():
            self.cmb_run_status.addItem(label, key)
        bar.addWidget(QLabel("群："))
        bar.addWidget(self.cmb_run_group)
        bar.addWidget(QLabel("状态："))
        bar.addWidget(self.cmb_run_status)
        bar.addStretch(1)
        btn_reload = QPushButton("刷新")
        btn_regen = QPushButton("重新生成")
        btn_regen.setObjectName("accent")
        btn_open = QPushButton("打开文档")
        btn_reveal = QPushButton("打开所在文件夹")
        for b in (btn_reload, btn_regen, btn_open, btn_reveal):
            b.setCursor(Qt.PointingHandCursor)
            bar.addWidget(b)
        lay.addLayout(bar)

        self.tbl_runs = _make_table(["时间", "群", "日期", "状态", "消息数",
                                     "文档"])
        self.tbl_runs.doubleClicked.connect(lambda _i: self._open_run_doc())
        lay.addWidget(self.tbl_runs, 1)

        tip = QLabel("双击一行打开该次总结的 Word；「重新生成」会按记录里的"
                     "日期重新总结同一批消息（会覆盖原文档）。")
        tip.setObjectName("hint")
        lay.addWidget(tip)

        btn_reload.clicked.connect(self.reload)
        btn_regen.clicked.connect(self._regenerate)
        btn_open.clicked.connect(self._open_run_doc)
        btn_reveal.clicked.connect(lambda: self._run_path(reveal=True))
        self.cmb_run_group.currentIndexChanged.connect(self.reload)
        self.cmb_run_status.currentIndexChanged.connect(self.reload)
        return page

    # ---- 归档文件 ----
    def _tab_files(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.cmb_file_group = QComboBox()
        self.cmb_file_group.setMinimumWidth(180)
        bar.addWidget(QLabel("群："))
        bar.addWidget(self.cmb_file_group)
        bar.addStretch(1)
        btn_reload = QPushButton("刷新")
        btn_open = QPushButton("打开文件")
        btn_reveal = QPushButton("打开所在文件夹")
        btn_export = QPushButton("导出 CSV")
        btn_export.setObjectName("accent")
        btn_root = QPushButton("打开输出总目录")
        for b in (btn_reload, btn_open, btn_reveal, btn_export, btn_root):
            b.setCursor(Qt.PointingHandCursor)
            bar.addWidget(b)
        lay.addLayout(bar)

        self.tbl_files = _make_table(["日期", "群", "文件名", "大小", "状态",
                                      "保存路径"])
        self.tbl_files.doubleClicked.connect(lambda _i: self._open_file())
        lay.addWidget(self.tbl_files, 1)

        self.lb_file_stat = QLabel("")
        self.lb_file_stat.setObjectName("hint")
        lay.addWidget(self.lb_file_stat)

        btn_reload.clicked.connect(self.reload)
        btn_open.clicked.connect(self._open_file)
        btn_reveal.clicked.connect(lambda: self._file_path(reveal=True))
        btn_export.clicked.connect(self._export_csv)
        btn_root.clicked.connect(lambda: _open(Path(OUTPUT_DIR)))
        self.cmb_file_group.currentIndexChanged.connect(self.reload)
        return page

    # ---- 日志与错误 ----
    def _tab_logs(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("错误记录（state.db 的 error_log）"))
        bar.addStretch(1)
        self.chk_follow = QCheckBox("自动滚动")
        self.chk_follow.setChecked(True)
        btn_reload = QPushButton("刷新")
        btn_clear = QPushButton("清空错误记录")
        btn_open_log = QPushButton("打开日志文件")
        btn_diag = QPushButton("导出诊断包")
        btn_diag.setObjectName("accent")
        for b in (btn_reload, btn_clear, btn_open_log, btn_diag):
            b.setCursor(Qt.PointingHandCursor)
            bar.addWidget(b)
        bar.addWidget(self.chk_follow)
        lay.addLayout(bar)

        split = QSplitter(Qt.Vertical)
        self.tbl_errors = _make_table(["时间", "模块", "级别", "内容"])
        split.addWidget(self.tbl_errors)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setPlaceholderText("运行日志（logs/app.log）")
        split.addWidget(self.txt_log)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        lay.addWidget(split, 1)

        btn_reload.clicked.connect(self.reload)
        btn_clear.clicked.connect(self._clear_errors)
        btn_open_log.clicked.connect(
            lambda: _open(Path(LOGS_DIR) / "app.log"))
        btn_diag.clicked.connect(self.export_diagnostics)
        return page

    # ---- 自检与维护 ----
    def _tab_checks(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addStretch(1)
        btn_again = QPushButton("重新检测")
        btn_prune = QPushButton("清理解密缓存")
        btn_export = QPushButton("导出诊断包")
        btn_export.setObjectName("accent")
        btn_data = QPushButton("打开数据目录")
        for b in (btn_again, btn_prune, btn_export, btn_data):
            b.setCursor(Qt.PointingHandCursor)
            bar.addWidget(b)
        lay.addLayout(bar)

        self.lb_cache = QLabel("")
        self.lb_cache.setObjectName("hint")
        self.lb_cache.setWordWrap(True)
        lay.addWidget(self.lb_cache)

        self.tbl_checks = _make_table(["状态", "项目", "详情"])
        self.tbl_checks.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        lay.addWidget(self.tbl_checks, 1)

        tip = QLabel("诊断包内含运行日志、state.db 快照、脱敏后的配置与自检"
                     "结果；不含微信解密密钥与明文 API Key。")
        tip.setObjectName("hint")
        lay.addWidget(tip)

        btn_again.clicked.connect(self._reload_checks)
        btn_prune.clicked.connect(self._prune_cache)
        btn_export.clicked.connect(self.export_diagnostics)
        btn_data.clicked.connect(lambda: _open(Path(DATA_DIR)))
        return page

    # ---------- 加载 ----------
    def reload(self):
        self._reload_groups()
        self._reload_runs()
        self._reload_files()
        self._reload_errors()
        self._reload_checks()
        self._tail_log()

    def _reload_groups(self):
        """群过滤器内容（保持当前选择）。"""
        groups = self._store.list_groups()
        self._group_names = {g["chatroom_id"]: g["group_name"] for g in groups}
        for combo in (self.cmb_run_group, self.cmb_file_group):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("全部群", None)
            for g in groups:
                combo.addItem(g["group_name"], g["chatroom_id"])
            idx = combo.findData(current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def _name_of(self, chatroom: str) -> str:
        return self._group_names.get(chatroom) or f"{chatroom[:10]}…"

    def _reload_runs(self):
        rows = self._store.list_summary_runs(
            chatroom_id=self.cmb_run_group.currentData(),
            status=self.cmb_run_status.currentData())
        self.tbl_runs.setSortingEnabled(False)
        self.tbl_runs.setRowCount(len(rows))
        for i, run in enumerate(rows):
            status = _RUN_STATUS.get(run.get("status") or "", run.get("status"))
            doc = run.get("docx_path") or ""
            cells = [run.get("created_at") or "-",
                     self._name_of(run.get("chatroom_id") or ""),
                     run.get("date") or "-", status or "-",
                     str(run.get("msg_count") or 0),
                     Path(doc).name if doc else "—"]
            for c, text in enumerate(cells):
                item = _cell(text, run)
                if c == 3:
                    item.setForeground(QColor(
                        "#07C160" if run.get("status") == "done"
                        else "#E0524A" if run.get("status") == "failed"
                        else "#E6A23C"))
                self.tbl_runs.setItem(i, c, item)
        self.tbl_runs.setSortingEnabled(True)
        _fit_columns(self.tbl_runs)

    def _reload_files(self):
        rows = self._store.list_archived_files(
            chatroom_id=self.cmb_file_group.currentData())
        self.tbl_files.setSortingEnabled(False)
        self.tbl_files.setRowCount(len(rows))
        total = 0
        for i, row in enumerate(rows):
            total += int(row.get("size") or 0)
            status = _FILE_STATUS.get(row.get("status") or "", row.get("status"))
            cells = [row.get("date") or "-",
                     self._name_of(row.get("chatroom_id") or ""),
                     row.get("orig_name") or "-",
                     maintenance.format_size(row.get("size") or 0),
                     status or "-", row.get("saved_path") or "-"]
            for c, text in enumerate(cells):
                self.tbl_files.setItem(i, c, _cell(text, row))
        self.tbl_files.setSortingEnabled(True)
        _fit_columns(self.tbl_files)
        self.lb_file_stat.setText(
            f"共 {len(rows)} 条记录，合计 {maintenance.format_size(total)}")

    def _reload_errors(self):
        rows = self._store.list_errors(300)
        self.tbl_errors.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for c, key in enumerate(("ts", "module", "level", "message")):
                item = _cell(row.get(key) or "")
                if c == 2 and str(row.get("level")).upper() == "ERROR":
                    item.setForeground(QColor("#E0524A"))
                self.tbl_errors.setItem(i, c, item)
        _fit_columns(self.tbl_errors)

    def _reload_checks(self):
        checks = maintenance.collect_checks()
        self.tbl_checks.setRowCount(len(checks))
        for i, item in enumerate(checks):
            level, color = _LEVEL.get(item["level"], _LEVEL["info"])
            cell0 = _cell(level)
            cell0.setForeground(QColor(color))
            self.tbl_checks.setItem(i, 0, cell0)
            self.tbl_checks.setItem(i, 1, _cell(item["name"]))
            self.tbl_checks.setItem(i, 2, _cell(item["detail"]))
        _fit_columns(self.tbl_checks, 420)
        self.lb_cache.setText(maintenance.build_size_hint())

    def _tail_log(self):
        if not self.isVisible():
            return
        text = _read_tail(Path(LOGS_DIR) / "app.log")
        if text != self.txt_log.toPlainText():
            bar = self.txt_log.verticalScrollBar()
            at_end = bar.value() >= bar.maximum() - 4
            self.txt_log.setPlainText(text)
            if self.chk_follow.isChecked() or at_end:
                self.txt_log.moveCursor(QTextCursor.End)
                bar.setValue(bar.maximum())

    # ---------- 行操作 ----------
    def _selected(self, table: QTableWidget) -> dict | None:
        row = table.currentRow()
        if row < 0:
            return None
        item = table.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    def _run_path(self, reveal: bool = False) -> Path | None:
        run = self._selected(self.tbl_runs)
        if not run:
            msgbox.info(self, "提示", "请先在上表选中一条总结记录。")
            return None
        doc = Path(run.get("docx_path") or "")
        if not doc.name:
            msgbox.warning(self, "没有文档",
                           "该条记录没有生成文档（可能失败了）。\n"
                           "可以点「重新生成」重试。")
            return None
        if not doc.exists():
            msgbox.warning(self, "文件不存在", f"文档已不在原位置：\n{doc}")
            return None
        (_reveal if reveal else _open)(doc)
        return doc

    def _open_run_doc(self):
        self._run_path()

    def _regenerate(self):
        run = self._selected(self.tbl_runs)
        if not run:
            msgbox.info(self, "提示", "请先在上表选中一条总结记录。")
            return
        chatroom = run.get("chatroom_id") or ""
        name = self._name_of(chatroom)
        date = run.get("date") or ""
        if not msgbox.question(
                self, "重新生成",
                f"按 {date} 的消息重新总结「{name}」？\n"
                "新文档会覆盖当天的旧文档。", yes="重新生成", no="取消"):
            return
        self.regenerate.emit(chatroom, name, date)

    def _file_path(self, reveal: bool = False) -> Path | None:
        row = self._selected(self.tbl_files)
        if not row:
            msgbox.info(self, "提示", "请先在上表选中一条归档记录。")
            return None
        path = Path(row.get("saved_path") or "")
        if not path.name or not path.exists():
            msgbox.warning(self, "文件不存在",
                           f"该文件未落盘或已被移动：\n{path}\n\n"
                           "（未下载/未归档类型的记录只用于排查）")
            return None
        (_reveal if reveal else _open)(path)
        return path

    def _open_file(self):
        self._file_path()

    def _export_csv(self):
        default = Path(OUTPUT_DIR) / f"归档清单_{datetime.now():%Y%m%d}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出归档清单", str(default), "CSV 文件 (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["日期", "群名", "文件名", "大小(字节)",
                                 "状态", "保存路径"])
                for i in range(self.tbl_files.rowCount()):
                    item = self.tbl_files.item(i, 0)
                    row = item.data(Qt.UserRole) if item else None
                    if not row:
                        continue
                    writer.writerow([
                        row.get("date"), self._name_of(row.get("chatroom_id")),
                        row.get("orig_name"), row.get("size"),
                        _FILE_STATUS.get(row.get("status") or "",
                                         row.get("status")),
                        row.get("saved_path")])
        except OSError as e:
            msgbox.warning(self, "导出失败", str(e))
            return
        msgbox.info(self, "导出完成", f"已导出 {self.tbl_files.rowCount()} 条"
                                     f"记录：\n{path}")

    def _clear_errors(self):
        if not msgbox.question(self, "清空错误记录",
                               "确定清空 state.db 里的错误记录？\n"
                               "（运行日志文件不受影响）",
                               yes="清空", no="取消"):
            return
        n = self._store.clear_errors()
        self._reload_errors()
        msgbox.info(self, "已清空", f"已删除 {n} 条错误记录。")

    # ---------- 维护 ----------
    def _prune_cache(self):
        info = maintenance.scan_dbcache()
        if not info["files"]:
            msgbox.info(self, "无需清理", "解密缓存目录当前是空的。")
            return
        if not msgbox.question(
                self, "清理解密缓存",
                f"当前占用 {maintenance.format_size(info['total'])}"
                f"（{info['files']} 个文件）。\n"
                "将按「保留天数 + 总量上限」策略删除旧的解密副本；"
                "这些文件可自动重建，删除不影响已生成的总结。\n"
                "建议在未进行总结时执行。",
                yes="开始清理", no="取消"):
            return
        freed, removed = maintenance.prune_dbcache()
        self._reload_checks()
        msgbox.info(self, "清理完成",
                    f"删除 {removed} 个文件，释放 "
                    f"{maintenance.format_size(freed)}。")

    def export_diagnostics(self):
        default = (Path(OUTPUT_DIR)
                   / f"WxSum诊断_{datetime.now():%Y%m%d_%H%M}.zip")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出诊断包", str(default), "Zip 压缩包 (*.zip)")
        if not path:
            return
        try:
            out = maintenance.export_diagnostics(Path(path))
        except Exception as e:                          # noqa: BLE001
            msgbox.warning(self, "导出失败", f"{type(e).__name__}: {e}")
            return
        msgbox.info(self, "诊断包已导出",
                    f"{out}\n\n包含运行日志、state.db 快照、脱敏配置与自检"
                    "结果；不含微信解密密钥与明文 API Key，可直接发给作者"
                    "排查问题。")

    # ---------- 生命周期 ----------
    def showEvent(self, ev):
        super().showEvent(ev)
        self.reload()
        self._log_timer.start()

    def closeEvent(self, ev):
        self._log_timer.stop()
        super().closeEvent(ev)

# -*- coding: utf-8 -*-
"""设置对话框：AI 服务商（预设下拉+自定义）+ 微信数据目录。

自绘无边框对话框（app.ui.frameless）：标题栏样式与主窗口一致，不再出现
原生标题栏左上角的小图标。
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialogButtonBox,
                               QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QToolButton,
                               QVBoxLayout)

from app.config import (AI_PROVIDERS, DEFAULT_ALERT_KEYWORDS, get_ai_config,
                        get_alert_config, get_auto_archive,
                        get_dbcache_config, get_ocr_enabled,
                        get_wechat_data_dir, normalize_wechat_dir,
                        save_ai_config, save_alert_config, save_auto_archive,
                        save_dbcache_config, save_ocr_enabled,
                        save_wechat_data_dir)
from app.core.alerts import parse_keywords, parse_silence
from app.ui import msgbox
from app.ui.collapse import CollapsibleSection
from app.ui.frameless import FramelessDialog

_DIALOG_QSS = """
QLabel { color: #2B3A35; font-size: 13px; }
QLabel#hint { color: #7A8683; font-size: 12px; }
QLineEdit, QComboBox, QSpinBox {
    border: 1px solid #DCE2E0; border-radius: 8px; padding: 6px 10px;
    font-size: 13px; background: #FFFFFF; color: #2B3A35;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #07C160; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: #FFFFFF; border: 1px solid #DCE2E0;
    selection-background-color: #E4F6EC; selection-color: #1F2D2A;
}
QPushButton {
    background: #F1F5F3; color: #2B3A35; border: none;
    border-radius: 8px; padding: 6px 14px; font-size: 12px;
}
QPushButton:hover { background: #E2EAE6; }
QToolButton#eyeBtn {
    background: transparent; border: none; padding: 0;
}
QToolButton#eyeBtn:hover { background: #E4F6EC; border-radius: 6px; }
QToolButton#eyeBtn:disabled { background: transparent; }
QCheckBox { color: #2B3A35; font-size: 12px; spacing: 6px; }
"""

# feather 风格眼睛图标（描边色随明暗状态切换）
_EYE_OPEN = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round">'
    '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>'
    '<circle cx="12" cy="12" r="3"/></svg>')
_EYE_OFF = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round">'
    '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8'
    'a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 '
    '11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>'
    '<line x1="1" y1="1" x2="23" y2="23"/></svg>')


def _eye_icon(kind: str, color: str) -> QIcon:
    svg = (_EYE_OPEN if kind == "open" else _EYE_OFF).format(c=color)
    pm = QPixmap()
    pm.loadFromData(QByteArray(svg.encode("utf-8")), "SVG")
    return QIcon(pm)


class SettingsDialog(FramelessDialog):
    def __init__(self, parent=None):
        super().__init__(parent, title="设置", background="#F3F5F4")
        self.resize_content(560, 340)
        self.setStyleSheet(_DIALOG_QSS)
        self._ai_changed = False
        self._wechat_dir_changed = False
        self._archive_changed = False
        self._ocr_changed = False
        self._alert_changed = False
        self._cache_changed = False

        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(18, 6, 18, 14)
        self.body_layout.addLayout(layout)

        cur_key, cur_url, cur_model, cur_think = get_ai_config()
        # AI 没配好就默认展开这一组，配好了则全部收起、只留四行带摘要的
        # 标题（点开才展开），避免一屏全铺开
        ai_ready = bool(cur_url and cur_model
                        and (cur_key or "localhost" in cur_url
                             or "127.0.0.1" in cur_url))
        self.sec_ai = self._add_section(layout, "AI 服务", not ai_ready)
        self.sec_archive = self._add_section(layout, "微信与归档", False)
        self.sec_alert = self._add_section(layout, "消息提醒", False)
        self.sec_cache = self._add_section(layout, "缓存与维护", False)

        # ---- AI 服务 ----
        sec = self.sec_ai.body_layout
        sec.addWidget(QLabel("AI 服务商"))
        self.cmb_provider = QComboBox()
        for p in AI_PROVIDERS:
            self.cmb_provider.addItem(p["name"], p)
        idx = self._match_provider(cur_url, cur_model)
        self.cmb_provider.setCurrentIndex(idx)
        self.cmb_provider.currentIndexChanged.connect(self._on_provider_changed)
        sec.addWidget(self.cmb_provider)

        sec.addWidget(QLabel("API Key"))
        key_row = QHBoxLayout()
        key_row.setSpacing(6)
        self.edit_key = QLineEdit()
        self.edit_key.setPlaceholderText("sk-...（本地 Ollama 无需填写）")
        if cur_key:
            self.edit_key.setText(cur_key)
            self.edit_key.setEchoMode(QLineEdit.Password)
        self.edit_key.textChanged.connect(self._mark_ai_changed)
        key_row.addWidget(self.edit_key, 1)

        # 小眼睛：点击在掩码/明文间切换，方便核对 Key 是否正确
        self.btn_eye = QToolButton()
        self.btn_eye.setObjectName("eyeBtn")
        self.btn_eye.setFixedSize(30, 30)
        self.btn_eye.setCursor(Qt.PointingHandCursor)
        self.btn_eye.setToolTip("显示 / 隐藏 API Key")
        self._key_visible = not bool(cur_key)   # 有现存Key默认掩码
        self._refresh_eye_icon()
        self.btn_eye.clicked.connect(self._toggle_key_visible)
        key_row.addWidget(self.btn_eye)
        sec.addLayout(key_row)

        sec.addWidget(QLabel("接口地址（base_url）"))
        self.edit_url = QLineEdit(cur_url)
        self.edit_url.textChanged.connect(self._mark_ai_changed)
        sec.addWidget(self.edit_url)

        sec.addWidget(QLabel("模型名（model）"))
        self.edit_model = QLineEdit(cur_model)
        self.edit_model.setPlaceholderText("如 deepseek-chat / glm-4-flash")
        self.edit_model.textChanged.connect(self._mark_ai_changed)
        self.edit_model.textChanged.connect(self._sync_summaries)
        sec.addWidget(self.edit_model)

        self.chk_think = QCheckBox(
            "关闭深度思考模式（更快更省，群消息总结建议开启）")
        self.chk_think.setChecked(bool(cur_think))
        self.chk_think.toggled.connect(self._mark_ai_changed)
        sec.addWidget(self.chk_think)
        ai_tip = QLabel("切换服务商自动填充地址与模型，也可手动修改；"
                        "协议均为 OpenAI 兼容格式。")
        ai_tip.setObjectName("hint")
        ai_tip.setWordWrap(True)
        sec.addWidget(ai_tip)

        # ---- 微信与归档 ----
        sec = self.sec_archive.body_layout
        sec.addWidget(QLabel("微信数据目录（一般保持自动检测即可）"))
        dir_row = QHBoxLayout()
        self.edit_dir = QLineEdit()
        self.edit_dir.setPlaceholderText("自动检测（推荐）")
        cur_dir = get_wechat_data_dir()
        if cur_dir:
            self.edit_dir.setText(cur_dir)
        self.edit_dir.setReadOnly(True)
        btn_browse = QPushButton("选择…")
        btn_browse.clicked.connect(self._browse)
        btn_clear = QPushButton("恢复自动")
        btn_clear.clicked.connect(self._clear_dir)
        dir_row.addWidget(self.edit_dir, 1)
        dir_row.addWidget(btn_browse)
        dir_row.addWidget(btn_clear)
        sec.addLayout(dir_row)

        self.chk_archive = QCheckBox(
            "自动归档群文件到 输出\\群名\\日期\\ 下（关闭后只生成总结，"
            "不复制文件）")
        self.chk_archive.setChecked(get_auto_archive())
        self.chk_archive.toggled.connect(self._mark_archive_changed)
        sec.addWidget(self.chk_archive)

        self.chk_ocr = QCheckBox(
            "对群聊图片做文字识别(OCR)后喂给 AI 总结"
            "（需微信已登录，首次识别稍慢，结果本地缓存）")
        self.chk_ocr.setChecked(get_ocr_enabled())
        self.chk_ocr.toggled.connect(self._mark_ocr_changed)
        sec.addWidget(self.chk_ocr)

        # ---- 消息提醒 ----
        sec = self.sec_alert.body_layout
        alert_on, alert_kw, alert_silence = get_alert_config()
        self.chk_alert = QCheckBox(
            "群消息命中关键词时弹托盘提醒（点提醒可直接打开当日总结）")
        self.chk_alert.setChecked(alert_on)
        self.chk_alert.toggled.connect(self._mark_alert_changed)
        sec.addWidget(self.chk_alert)

        kw_row = QHBoxLayout()
        kw_row.setSpacing(8)
        lb_kw = QLabel("提醒关键词")
        lb_kw.setMinimumWidth(76)
        self.edit_keywords = QLineEdit(alert_kw)
        self.edit_keywords.setPlaceholderText(DEFAULT_ALERT_KEYWORDS)
        self.edit_keywords.textChanged.connect(self._mark_alert_changed)
        kw_row.addWidget(lb_kw)
        kw_row.addWidget(self.edit_keywords, 1)
        sec.addLayout(kw_row)

        sil_row = QHBoxLayout()
        sil_row.setSpacing(8)
        lb_sil = QLabel("免打扰时段")
        lb_sil.setMinimumWidth(76)
        self.edit_silence = QLineEdit(alert_silence)
        self.edit_silence.setPlaceholderText("如 22:00-07:00（留空=不静默）")
        self.edit_silence.setMaximumWidth(180)
        self.edit_silence.textChanged.connect(self._mark_alert_changed)
        sil_row.addWidget(lb_sil)
        sil_row.addWidget(self.edit_silence)
        sil_row.addStretch(1)
        sec.addLayout(sil_row)

        hint_alert = QLabel("多个关键词用逗号分隔；免打扰时段支持跨午夜，"
                            "如 22:00-07:00。")
        hint_alert.setObjectName("hint")
        hint_alert.setWordWrap(True)
        sec.addWidget(hint_alert)

        # ---- 缓存与维护 ----
        sec = self.sec_cache.body_layout
        cache_days, cache_mb = get_dbcache_config()
        cache_row = QHBoxLayout()
        cache_row.setSpacing(8)
        lb_cache = QLabel("缓存保留")
        lb_cache.setMinimumWidth(76)
        self.spin_cache_days = QSpinBox()
        self.spin_cache_days.setRange(0, 365)
        self.spin_cache_days.setSuffix(" 天")
        self.spin_cache_days.setValue(cache_days)
        self.spin_cache_days.setMaximumWidth(110)
        self.spin_cache_mb = QSpinBox()
        self.spin_cache_mb.setRange(0, 102400)
        self.spin_cache_mb.setSingleStep(512)
        self.spin_cache_mb.setSuffix(" MB")
        self.spin_cache_mb.setValue(cache_mb)
        self.spin_cache_mb.setMaximumWidth(130)
        for w in (self.spin_cache_days, self.spin_cache_mb):
            w.valueChanged.connect(self._mark_cache_changed)
        cache_row.addWidget(lb_cache)
        cache_row.addWidget(self.spin_cache_days)
        cache_row.addWidget(QLabel("上限"))
        cache_row.addWidget(self.spin_cache_mb)
        cache_row.addStretch(1)
        sec.addLayout(cache_row)
        hint_cache = QLabel("解密缓存是微信库的只读副本，会随库增长；"
                            "按上面的天数/上限自动清理，0 = 不限制。"
                            "也可在「记录与诊断」里手动清理。")
        hint_cache.setObjectName("hint")
        hint_cache.setWordWrap(True)
        sec.addWidget(hint_cache)

        buttons = QDialogButtonBox(QDialogButtonBox.Save
                                   | QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Close).setText("关闭")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._sync_key_state()
        self._sync_summaries()
        self._fit_height()

    # ---------- 折叠分组 ----------
    def _add_section(self, layout: QVBoxLayout, title: str,
                     expanded: bool) -> CollapsibleSection:
        sec = CollapsibleSection(title, expanded=expanded, parent=self)
        sec.reflow.connect(self._fit_height)
        layout.addWidget(sec)
        return sec

    def _fit_height(self) -> None:
        """折叠/展开后按内容调整窗口高度（宽度保持不变）。"""
        lay = self.layout()
        if lay is not None:
            lay.activate()
        target = max(self.sizeHint().height(), self.minimumSizeHint().height())
        if self.height() != target:
            self.resize(self.width(), target)

    def _sync_summaries(self, *_):
        """把当前配置压缩成一句摘要显示在分组标题右侧。"""
        model = self.edit_model.text().strip()
        if hasattr(self, "sec_ai"):
            self.sec_ai.set_summary(model or "未配置")
        if hasattr(self, "sec_archive"):
            self.sec_archive.set_summary(
                "{} · 归档{} · OCR{}".format(
                    "已指定目录" if self.edit_dir.text().strip() else "自动检测",
                    "开" if self.chk_archive.isChecked() else "关",
                    "开" if self.chk_ocr.isChecked() else "关"))
        if hasattr(self, "sec_alert"):
            if self.chk_alert.isChecked():
                text = f"已开启 · {len(parse_keywords(self.edit_keywords.text()))} 个词"
                silence = self.edit_silence.text().strip()
                if silence:
                    text += f" · 静默 {silence}"
            else:
                text = "已关闭"
            self.sec_alert.set_summary(text)
        if hasattr(self, "sec_cache"):
            days, mb = self.spin_cache_days.value(), self.spin_cache_mb.value()
            self.sec_cache.set_summary(
                f"保留 {'不限' if days == 0 else f'{days} 天'} · "
                f"上限 {'不限' if mb == 0 else f'{mb} MB'}")

    # ---------- 服务商联动 ----------
    def _match_provider(self, base_url: str, model: str = "") -> int:
        """按地址+模型匹配预设；匹配不上落到「自定义」。"""
        base_url = (base_url or "").rstrip("/")
        model = (model or "").strip()
        # 地址相同的预设（如两个智谱）再用模型名区分
        for i, p in enumerate(AI_PROVIDERS):
            if p["id"] == "custom":
                continue
            if p["base_url"].rstrip("/") == base_url and (
                    not model or p["model"] == model):
                return i
        # 只匹配到地址（用户改过模型名）也回选对应服务商
        for i, p in enumerate(AI_PROVIDERS):
            if p["id"] != "custom" and p["base_url"].rstrip("/") == base_url:
                return i
        return len(AI_PROVIDERS) - 1

    def _on_provider_changed(self, _idx: int):
        p = self.cmb_provider.currentData()
        if p["id"] == "custom":
            self.edit_url.setFocus()
        else:
            self.edit_url.setText(p["base_url"])
            self.edit_model.setText(p["model"])
        # 预设推荐关思考（智谱推理模型）则自动勾选；其余预设取消勾选
        self.chk_think.setChecked(bool(p.get("thinking_disabled")))
        self._sync_key_state()
        self._mark_ai_changed()

    def _sync_key_state(self):
        need_key = self.cmb_provider.currentData()["need_key"]
        self.edit_key.setEnabled(need_key)
        self.btn_eye.setEnabled(need_key)
        if not need_key:
            self.edit_key.setPlaceholderText("本地服务无需 API Key")
        self._refresh_eye_icon()

    def _refresh_eye_icon(self):
        """明文=睁眼，掩码=闭眼；禁用时灰色。"""
        enabled = self.btn_eye.isEnabled()
        color = "#7A8683" if enabled else "#BFC7C4"
        kind = "open" if self._key_visible else "off"
        self.btn_eye.setIcon(_eye_icon(kind, color))
        self.btn_eye.setIconSize(QSize(18, 18))

    def _toggle_key_visible(self):
        self._key_visible = not self._key_visible
        self.edit_key.setEchoMode(
            QLineEdit.Normal if self._key_visible else QLineEdit.Password)
        self._refresh_eye_icon()

    def _mark_ai_changed(self, *_):
        self._ai_changed = True

    def _mark_archive_changed(self, *_):
        self._archive_changed = True
        self._sync_summaries()

    def _mark_ocr_changed(self, *_):
        self._ocr_changed = True
        self._sync_summaries()

    def _mark_cache_changed(self, *_):
        self._cache_changed = True
        self._sync_summaries()

    def _mark_alert_changed(self, *_):
        self._alert_changed = True
        self._sync_summaries()

    # ---------- 微信目录 ----------
    def _browse(self):
        picked = QFileDialog.getExistingDirectory(
            self, "选择微信数据目录（xwechat_files 或其上级目录）")
        if not picked:
            return
        norm = normalize_wechat_dir(picked)
        if not norm:
            msgbox.warning(
                self, "目录无效",
                "所选目录下未找到微信账号数据（应包含 "
                "xwechat_files\\<账号>\\db_storage）。\n请重新选择。")
            return
        self.edit_dir.setText(norm)
        self._wechat_dir_changed = True
        self._sync_summaries()

    def _clear_dir(self):
        self.edit_dir.clear()
        self._wechat_dir_changed = True
        self._sync_summaries()

    # ---------- 保存 ----------
    def _save(self):
        key = self.edit_key.text().strip()
        url = self.edit_url.text().strip().rstrip("/")
        model = self.edit_model.text().strip()
        p = self.cmb_provider.currentData()

        if not url or not model:
            msgbox.warning(self, "信息不完整",
                           "请填写接口地址和模型名。")
            return
        if p["need_key"] and not key:
            if not msgbox.question(
                    self, "缺少 API Key",
                    "当前服务商需要 API Key 但未填写，保存后 AI 总结将不可用。\n"
                    "仍然保存？", yes="仍然保存", no="返回修改"):
                return

        save_ai_config(key, url, model,
                       disable_thinking=self.chk_think.isChecked())
        if self._wechat_dir_changed:
            save_wechat_data_dir(self.edit_dir.text().strip())
        if self._archive_changed:
            save_auto_archive(self.chk_archive.isChecked())
        if self._ocr_changed:
            save_ocr_enabled(self.chk_ocr.isChecked())
        if self._alert_changed:
            silence = self.edit_silence.text().strip()
            if silence and parse_silence(silence) is None:
                msgbox.warning(
                    self, "时段格式不对",
                    "「免打扰时段」请按 22:00-07:00 的格式填写，"
                    "留空表示不静默。")
                return
            save_alert_config(self.chk_alert.isChecked(),
                              self.edit_keywords.text().strip(), silence)
        if self._cache_changed:
            save_dbcache_config(self.spin_cache_days.value(),
                                self.spin_cache_mb.value())
        msgbox.info(self, "已保存", "设置已保存。")
        self.accept()

    def ai_config(self) -> tuple[str, str, str, bool] | None:
        """本次保存的 (key, url, model, disable_thinking)；未保存返回 None。"""
        if not self._ai_changed:
            return None
        return (self.edit_key.text().strip(),
                self.edit_url.text().strip().rstrip("/"),
                self.edit_model.text().strip(),
                self.chk_think.isChecked())

    def wechat_dir_changed(self) -> bool:
        return self._wechat_dir_changed

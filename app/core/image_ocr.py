# -*- coding: utf-8 -*-
"""图片 OCR：解密微信图片 → RapidOCR 提取文字 → 拼入消息内容喂给 AI。

设计要点：
- 懒加载 RapidOCR（首次 OCR 时才初始化，避免开屏卡顿）。
- 用 MediaDownloader.download_image 解密本地 .dat 为 jpg/png 临时文件，
  再交给 RapidOCR；识别失败/无图一律返回空串，不阻断总结。
- 结果按 chatroom_id:local_id 做 JSON 持久化缓存（图片不会变，重复总结
  不再重复解密+识别，省时间）。
- 关键：只缓存非空结果。缩略图（用户未在微信查看大图时只有缩略图）
  识别失败时不缓存，等下次原图可用后自动重试。
- 对过小的缩略图做 2x 放大再识别（聊胜于无）。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from app.config import DATA_DIR
from app.models import Message

log = logging.getLogger("app.ocr")

_CACHE_FILE = DATA_DIR / "ocr_cache.json"
# 缩略图判定阈值：任一边小于此值视为缩略图，需放大后再 OCR
_THUMB_PX = 400


class ImageOCR:
    """单例式图片 OCR 器（带持久化缓存）。"""

    _instance: "ImageOCR | None" = None

    def __init__(self):
        self._ocr = None
        self._cache: dict[str, str] = {}
        self._load_cache()

    @classmethod
    def instance(cls) -> "ImageOCR":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---------- 缓存 ----------
    def _load_cache(self):
        try:
            if _CACHE_FILE.exists():
                self._cache = json.loads(
                    _CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            self._cache = {}

    def _save_cache(self):
        try:
            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CACHE_FILE.write_text(
                json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    # ---------- OCR 引擎 ----------
    def _get_ocr(self):
        if self._ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            self._ocr = RapidOCR()
        return self._ocr

    # ---------- 对外 ----------
    def ocr_message(self, db, message: Message) -> str:
        """对一条图片消息做 OCR，返回提取的文字；失败/无图返回空串。"""
        if message.msg_type != "图片":
            return ""
        key = f"{message.chatroom_id}:{message.local_id}"
        if key in self._cache:
            return self._cache[key]

        text = ""
        try:
            from wechatauto.media import MediaDownloader
            md = MediaDownloader(db)
            with tempfile.TemporaryDirectory() as td:
                img_path = md.download_image(
                    message.chatroom_id, message.local_id, save_dir=td)
                if img_path and os.path.exists(img_path):
                    text = self._ocr_file(img_path)
        except Exception as e:                          # noqa: BLE001
            log.warning("OCR 图片 %s 失败: %s", message.local_id, e)

        # 只缓存非空结果：缩略图识别失败时不缓存，
        # 等用户在微信点开大图后下次总结可重试
        if text:
            self._cache[key] = text
            self._save_cache()
        else:
            log.info("图片 %s OCR 无文字（可能仅缩略图，"
                     "在微信中点开大图后下次总结可识别）",
                     message.local_id)
        return text

    def _ocr_file(self, path: str) -> str:
        # 过小的缩略图放大 2 倍再识别（原图分辨率足够则无需放大）
        try:
            from PIL import Image
            im = Image.open(path)
            if im.width < _THUMB_PX or im.height < _THUMB_PX:
                up = im.resize((im.width * 2, im.height * 2),
                               Image.LANCZOS)
                up_path = path + "_up.png"
                up.save(up_path)
                path = up_path
        except Exception:
            pass

        try:
            ocr = self._get_ocr()
            result, _ = ocr(path)
            if not result:
                return ""
            # result 每项为 [box, text, score]
            lines = [r[1] for r in result if len(r) >= 2 and r[1]]
            return "\n".join(lines).strip()
        except Exception as e:                          # noqa: BLE001
            log.warning("RapidOCR 识别失败: %s", e)
            return ""


def enrich_image_messages(db, messages: list[Message]) -> None:
    """就地把图片消息的 content 补上 OCR 文字：[图片] 识别文字: ..."""
    ocr = ImageOCR.instance()
    n_img = sum(1 for m in messages if m.msg_type == "图片")
    if n_img:
        log.info("开始对 %d 张图片做 OCR…", n_img)
    for m in messages:
        if m.msg_type != "图片":
            continue
        text = ocr.ocr_message(db, m)
        if text:
            m.content = f"[图片] 识别文字: {text}"

# -*- coding: utf-8 -*-
"""OpenAI 兼容聊天客户端（DeepSeek/智谱/Kimi/通义/Ollama 等通用）。

协议：POST {base_url}/chat/completions，Bearer 鉴权。
本地服务（如 Ollama）可以不传 api_key。

针对免费档（智谱 glm-4-flash 等）的限流做了专门处理：
- 进程内请求节流（最短间隔），避免多群/多分块连发触发 429；
- 429 读取 Retry-After，否则按 10s 起长退避，最多 5 次；
- 401/400 等 4xx 立即失败不重试；5xx/超时 3 次短退避；
- disable_thinking 给智谱系模型注入 thinking=disabled，关闭推理，
  显著降低延迟与 token 消耗。
"""
from __future__ import annotations

import threading
import time

import httpx

from app.config import (DEFAULT_AI_BASE_URL, DEFAULT_AI_MODEL,
                        DEEPSEEK_TIMEOUT_SEC)

# 限流退避：10s 起步翻倍，封顶 60s
_RATE_LIMIT_DELAYS = (10, 20, 40, 60, 60)
# 服务端错误/超时退避
_TRANSIENT_DELAYS = (2, 4, 8)
# 同一客户端相邻请求的最小间隔（秒），防免费档 QPS 限流
_MIN_REQUEST_INTERVAL = 2.5


class DeepSeekError(RuntimeError):
    pass


class DeepSeekClient:
    """保持历史类名；实际为任意 OpenAI 兼容服务的客户端。"""

    def __init__(self, api_key: str = "",
                 base_url: str = DEFAULT_AI_BASE_URL,
                 model: str = DEFAULT_AI_MODEL,
                 disable_thinking: bool = False):
        if not base_url:
            raise DeepSeekError("未配置 AI 接口地址（base_url）")
        if not model:
            raise DeepSeekError("未配置 AI 模型名（model）")
        self._key = (api_key or "").strip()
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._disable_thinking = bool(disable_thinking)
        self._last_send = 0.0
        self._lock = threading.Lock()

    def chat(self, system: str, user: str, temperature: float = 0.3) -> str:
        payload: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if self._disable_thinking:        # 智谱 GLM-4.5+ 关深度思考
            payload["thinking"] = {"type": "disabled"}
        headers = {"Content-Type": "application/json"}
        if self._key:                      # 本地 Ollama 等无需鉴权
            headers["Authorization"] = f"Bearer {self._key}"

        url = f"{self._base_url}/chat/completions"
        # 两类错误各自独立计数：429 长退避 5 次，其它瞬时错误 3 次
        rate_attempt = 0
        trans_attempt = 0
        while True:
            self._throttle()
            try:
                resp = httpx.post(url, headers=headers, json=payload,
                                  timeout=DEEPSEEK_TIMEOUT_SEC)
            except Exception as e:                  # 超时/连接错误
                trans_attempt += 1
                if trans_attempt > len(_TRANSIENT_DELAYS):
                    raise DeepSeekError(
                        f"AI 接口连接失败（已重试{trans_attempt - 1}次）: {e}")
                time.sleep(_TRANSIENT_DELAYS[trans_attempt - 1])
                continue

            if resp.status_code == 200:
                try:
                    return resp.json()["choices"][0]["message"]["content"]
                except Exception as e:              # noqa: BLE001
                    raise DeepSeekError(f"AI 返回内容解析失败: {e}")

            # 429 限流：免费档最常见，长退避
            if resp.status_code == 429:
                if rate_attempt >= len(_RATE_LIMIT_DELAYS):
                    raise DeepSeekError(
                        "AI 服务商持续限流（免费档有并发/频次限制），"
                        f"已等待重试{rate_attempt}次仍失败，请过几分钟再试，"
                        "或在设置中更换服务商。")
                wait = self._retry_after(resp) \
                    or _RATE_LIMIT_DELAYS[rate_attempt]
                rate_attempt += 1
                time.sleep(wait)
                continue

            # 4xx（401/403/404/400 等）立即失败，并给出可读提示
            if 400 <= resp.status_code < 500:
                raise DeepSeekError(self._client_error_hint(resp))

            # 5xx 服务端错误：短退避
            trans_attempt += 1
            if trans_attempt > len(_TRANSIENT_DELAYS):
                raise DeepSeekError(
                    f"AI 服务端错误 HTTP {resp.status_code}（已重试"
                    f"{trans_attempt - 1}次）: {resp.text[:200]}")
            time.sleep(_TRANSIENT_DELAYS[trans_attempt - 1])

    # ---------- 内部 ----------
    def _throttle(self) -> None:
        """保证相邻请求至少间隔 _MIN_REQUEST_INTERVAL 秒。"""
        with self._lock:
            gap = time.time() - self._last_send
            if gap < _MIN_REQUEST_INTERVAL:
                time.sleep(_MIN_REQUEST_INTERVAL - gap)
            self._last_send = time.time()

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        """解析 Retry-After（秒数或 HTTP 日期）。"""
        val = resp.headers.get("Retry-After")
        if not val:
            return None
        try:
            return max(1.0, float(val))
        except ValueError:
            pass
        try:
            from email.utils import parsedate_to_datetime
            w = parsedate_to_datetime(val)
            return max(1.0, w.timestamp() - time.time())
        except Exception:
            pass
        return None

    def _client_error_hint(self, resp: httpx.Response) -> str:
        code = resp.status_code
        detail = ""
        try:
            err = resp.json().get("error")
            detail = (err.get("message") if isinstance(err, dict)
                      else str(err)) or resp.text[:200]
        except Exception:
            detail = resp.text[:200]
        if code in (401, 403):
            return (f"AI 鉴权失败 HTTP {code}：API Key 不正确或未实名认证。"
                    f"（{detail}）")
        if code == 404:
            return (f"AI 接口或模型不存在 HTTP 404：请检查接口地址与模型名"
                    f"「{self._model}」。（{detail}）")
        return f"AI 接口拒绝请求 HTTP {code}: {detail}"

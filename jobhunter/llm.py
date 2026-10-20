import json
import os
import re
import threading
import time

import httpx

_SLOTS = threading.BoundedSemaphore(int(os.environ.get("JOBHUNTER_LLM_CONCURRENCY", "4")))


class _Retryable(RuntimeError):
    pass


class LLM:
    """OpenAI 兼容的 Chat Completions 客户端（默认 OpenRouter）。"""

    def __init__(self, cfg: dict):
        self.base_url = cfg["base_url"].rstrip("/")
        self.model = cfg["model"]
        self.api_key = cfg.get("api_key") or os.environ.get(cfg.get("api_key_env", "OPENROUTER_API_KEY"), "")

    def chat(self, system: str, user: str, temperature: float = 0.3, timeout: float = 90) -> str:
        # 全局限制同时在途的请求数：DeepSeek 按余额限制并发（余额低时只有 5 个），
        # 插件多个网站同时打分 + 后台生成简历很容易超
        with _SLOTS:
            for attempt in range(3):
                try:
                    return self._post(system, user, temperature, timeout)
                except (_Retryable, httpx.TimeoutException, httpx.TransportError) as e:
                    if attempt == 2:
                        raise RuntimeError(f"{e}（已重试 3 次）") from e
                    time.sleep((2, 6)[attempt])
        raise AssertionError("unreachable")

    def _post(self, system: str, user: str, temperature: float, timeout: float) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json={
                "model": self.model,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=timeout,
        )
        if resp.status_code >= 400:  # 把服务商返回的原因带出来（余额不足、模型不可用等）
            try:
                reason = resp.json().get("error", {}).get("message") or resp.text
            except ValueError:
                reason = resp.text
            msg = f"AI 接口返回 {resp.status_code}：{str(reason)[:200]}"
            if resp.status_code == 429 or resp.status_code >= 500:  # 限流 / 服务端繁忙：稍后重试
                raise _Retryable(msg)
            raise RuntimeError(msg)
        return resp.json()["choices"][0]["message"]["content"]

    def chat_json(self, system: str, user: str) -> dict:
        text = self.chat(system, user + "\n\n只输出 JSON，不要任何其他文字。")
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError(f"模型未返回 JSON: {text[:200]}")
        return json.loads(match.group(0))

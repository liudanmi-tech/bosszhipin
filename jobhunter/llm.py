import json
import os
import re

import httpx


class LLM:
    """OpenAI 兼容的 Chat Completions 客户端（默认 OpenRouter）。"""

    def __init__(self, cfg: dict):
        self.base_url = cfg["base_url"].rstrip("/")
        self.model = cfg["model"]
        self.api_key = cfg.get("api_key") or os.environ.get(cfg.get("api_key_env", "OPENROUTER_API_KEY"), "")

    def chat(self, system: str, user: str, temperature: float = 0.3) -> str:
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
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def chat_json(self, system: str, user: str) -> dict:
        text = self.chat(system, user + "\n\n只输出 JSON，不要任何其他文字。")
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError(f"模型未返回 JSON: {text[:200]}")
        return json.loads(match.group(0))

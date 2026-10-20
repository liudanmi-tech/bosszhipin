"""网页上可选的 AI 服务商（都是 OpenAI 兼容接口）。每家的 Key 和模型分开保存，切换不丢。"""

PROVIDERS = {
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "key_field": "api_key",
        "model_field": "model",
        "key_url": "https://openrouter.ai/keys",
        "key_placeholder": "sk-or-...",
        "models": [
            ["deepseek/deepseek-v4.1-flash", "DeepSeek V4.1 Flash · 最便宜，推荐"],
            ["deepseek/deepseek-v4-pro", "DeepSeek V4 Pro · 更强，略贵"],
            ["anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5 · 效果最好，最贵"],
        ],
    },
    "deepseek": {
        "label": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com",
        "key_field": "deepseek_api_key",
        "model_field": "deepseek_model",
        "key_url": "https://platform.deepseek.com/api_keys",
        "key_placeholder": "sk-...",
        "models": [
            ["deepseek-flash", "DeepSeek Flash · 便宜，推荐"],
            ["deepseek-v4-pro", "DeepSeek V4 Pro · 更强，略贵"],
        ],
    },
}

KEY_FIELDS = {p["key_field"] for p in PROVIDERS.values()}


def default_settings() -> dict:
    out = {"provider": "openrouter"}
    for p in PROVIDERS.values():
        out[p["key_field"]] = ""
        out[p["model_field"]] = p["models"][0][0]
    return out


def public_info() -> list[dict]:
    """给网页用的服务商列表（不含 Key）。"""
    return [{"name": k, **{f: v[f] for f in ("label", "key_field", "model_field", "key_url", "key_placeholder", "models")}}
            for k, v in PROVIDERS.items()]

# -*- coding: utf-8 -*-
"""
mock_provider —— 离线示例 AI 接口。

不联网，直接回显/模拟回复。用于：
    1. 无 API Key 时验证整条链路（聊天、文件处理、会话保存）；
    2. 作为编写新插件的极简参考。

实现规范：
    @register_ai_provider(...) 装饰 chat(messages, temperature) 函数即可。
"""
from __future__ import annotations

import os
from pickle import FALSE
from typing import Any

from core.plugin_manager import register_ai_provider, load_plugin_config
from .main import main

_CONFIG = load_plugin_config(__file__)

PARAMS_SCHEMA: dict[str, dict[str, Any]] = {
    "api_key": {
        "type": "string",
        "name": "API Key（接口秘钥）",
        "description": "OpenAI / DeepSeek / Moonshot 等服务的密钥。敏感字段，仅保存在本机。",
        "sensitive": True,
        "default": "",
    },
    "model": {
        "type": "string",
        "name": "模型名称",
        "description": "例如 deepseek-v4-flash",
        "default": "deepseek-v4-flash",
    },
    "base_url": {
        "type": "string",
        "name": "Base URL",
        "description": "例如 https://api.openai.com/v1",
        "default": "https://api.deepseek.com/v1",
    },
}


@register_ai_provider(
    provider_id="deepseek",
    name="DeepSeek",
    description="不",
    params=PARAMS_SCHEMA,
    supports_images=False,
)
def chat(messages: list, temperature: float = 0.7, extra_body: dict | None = None) -> str:
    """
    模拟对话：取出最后一条用户消息并构造回复。

    :param messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
    :param temperature: 保留参数（mock 不使用）
    :param extra_body: 用户在设置页填写的自定义参数（api_key/base_url/model/...）
    :return: 纯文本回复
    """
    # 取最后一条 user 消息
    # user_text = ""
    # for m in reversed(messages or []):
    #     if m.get("role") == "user":
    #         user_text = m.get("content", "")
    #         break
    #
    # if len(user_text) > 120:
    #     user_text = user_text[:120] + "…"
    extra = extra_body if isinstance(extra_body, dict) else {}

    def pick(key: str, env_name: str, default: Any):
        """extra_body > 环境变量 > config.json > default"""
        print(extra)
        val = extra.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            val = os.environ.get(env_name) or _CONFIG.get(key, default)
        return val

    api_key = str(pick("api_key", "EXAMPLE_PROVIDER_API_KEY", "") or "").strip()
    model = str(pick("model", "EXAMPLE_PROVIDER_MODEL", "deepseek-v4-flash"))
    base_url = str(pick("base_url", "EXAMPLE_PROVIDER_BASE_URL", "https://api.deepseek.com/v1"))
    return main(messages,model_name=model,api_key=api_key,temperature=temperature,base_url=base_url)

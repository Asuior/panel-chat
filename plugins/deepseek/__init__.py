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

# 该接口专属的“空对话欢迎页”（可选能力的用法演示）。
# 提供 welcome_html 后：聊天窗在没有任何消息时用它替换内置默认欢迎词；
# 切换到未提供 welcome_html 的接口（如 ModelScope）会自动回落为内置默认。
# 注意：欢迎页顶部的头像位（.big）由程序渲染、跟随“设置 → 外观”的 AI 头像，
# 不属于 welcome_html 的范围，所以这里不要自己写 .big；
# 内容部分可复用内置类 h2 / p / .chips 以继承同样的样式，
# 其中带 data-tab="ai|prompts|..." 的按钮由前端自动绑定为“跳转对应设置页签”。
WELCOME_HTML = """
<h2>你好，我是 DeepSeek 助手</h2>
<p>· 拖拽文件到悬浮球，AI 处理并写回原文件<br>
· 消息可编辑：修改最新提问将自动重新生成<br>
· 消息可删除：其后内容一并截断<br>
· 支持 Markdown 与代码高亮</p>
<div class="chips">
<button class="btn" data-tab="ai">选择 AI 接口</button>
<button class="btn" data-tab="prompts">管理系统提示词</button>
</div>
""".strip()


@register_ai_provider(
    provider_id="deepseek",
    name="DeepSeek",
    description="不",
    params=PARAMS_SCHEMA,
    supports_images=False,
    welcome_html=WELCOME_HTML,
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

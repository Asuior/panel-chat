# -*- coding: utf-8 -*-
"""
DeepSeek 接口 —— 基于 LangGraph 的 Agent（对话 ↔ 工具循环）。

思考模式：
    前端在「设置 → AI 接口」里渲染 PARAMS_SCHEMA 中的 thinking 开关，
    取值经 extra_body 透传给 ChatDeepSeek，最终以
    ``{"thinking": {"type": "enabled"|"disabled"}}`` 出现在请求体顶层
    （见 plugins/deepseek/main.py 的 chat_node）。

实现规范：
    @register_ai_provider(...) 装饰 chat(messages, temperature, extra_body) 函数即可。
"""
from __future__ import annotations

import os
from typing import Any

from core.plugin_manager import register_ai_provider, load_plugin_config, coerce_params
from .main import main

_CONFIG = load_plugin_config(__file__)

PARAMS_SCHEMA: dict[str, dict[str, Any]] = {
    "api_key": {
        "type": "string",
        "name": "API Key（接口秘钥）",
        "description": "DeepSeek 服务的密钥。敏感字段，仅保存在本机。",
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
        "description": "例如 https://api.deepseek.com/v1",
        "default": "https://api.deepseek.com/v1",
    },
    "thinking": {
        "type": "boolean",
        "name": "思考模式",
        "description": (
            "开启或关闭思考模式。"
        ),
        "default": True,
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
<p>· 支持图片输入：粘贴、拖拽或点击上传<br>
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
    调用 DeepSeek Agent 完成一轮对话（内部可能包含多次工具调用）。

    :param messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
    :param temperature: 采样温度（思考模式下 API 会忽略它）
    :param extra_body: 用户在设置页填写的自定义参数（api_key/model/base_url/thinking）
    :return: 纯文本回复
    """
    extra = coerce_params(extra_body, PARAMS_SCHEMA)

    def pick(key: str, env_name: str, default: Any):
        """extra_body > 环境变量 > config.json > default

        注意判断方式：**只有 None 与空字符串**才算“用户没填”。
        不能写成 ``if not val``，否则 False / 0 这些显式取值会被当成
        未填写而回退到默认值，等于开关永远失效。
        """
        val = extra.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            val = os.environ.get(env_name)
            if val is None or (isinstance(val, str) and not val.strip()):
                val = _CONFIG.get(key, default)
        return val

    api_key = str(pick("api_key", "DEEPSEEK_API_KEY", "") or "").strip()
    model = str(pick("model", "DEEPSEEK_MODEL", "deepseek-v4-flash"))
    base_url = str(pick("base_url", "DEEPSEEK_API_BASE", "https://api.deepseek.com/v1"))
    # 思考模式开关 → DeepSeek 的请求体参数格式
    thinking = bool(pick("thinking", "DEEPSEEK_THINKING", True))
    extra_request = {"thinking": {"type": "enabled" if thinking else "disabled"}}
    return main(messages, model_name=model, api_key=api_key, temperature=temperature,
                base_url=base_url, extra_body=extra_request)

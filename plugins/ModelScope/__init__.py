# -*- coding: utf-8 -*-
"""
ModelScope 接口 —— 基于 LangGraph 的 Agent（对话 ↔ 工具循环）。

与 plugins/deepseek 结构相同，区别只在服务商与默认值；
声明了 supports_images，因此支持图片输入。

思考模式：
    前端在「设置 → AI 接口」里渲染 PARAMS_SCHEMA 中的 thinking 开关，
    取值经 extra_body 透传给 ChatDeepSeek，最终以
    ``{"thinking": {"type": "enabled"|"disabled"}}`` 出现在请求体顶层。

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
        "description": "ModelScope 创空间访问令牌。敏感字段，仅保存在本机。",
        "sensitive": True,
        "default": "",
    },
    "model": {
        "type": "string",
        "name": "模型名称",
        "description": "例如 Qwen/Qwen3.8-27B",
        "default": "Qwen/Qwen3.8-27B",
    },
    "base_url": {
        "type": "string",
        "name": "Base URL",
        "description": "例如 https://api-inference.modelscope.cn/v1",
        "default": "https://api-inference.modelscope.cn/v1",
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


@register_ai_provider(
    provider_id="ModelScope",
    name="ModelScope",
    description="",
    params=PARAMS_SCHEMA,
    supports_images=True,
)
def chat(messages: list, temperature: float = 0.7, extra_body: dict | None = None) -> str:
    """
    调用 ModelScope Agent 完成一轮对话（内部可能包含多次工具调用）。

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

    api_key = str(pick("api_key", "MODELSCOPE_API_KEY", "") or "").strip()
    model = str(pick("model", "MODELSCOPE_MODEL", "Qwen/Qwen3.8-27B"))
    base_url = str(pick("base_url", "MODELSCOPE_BASE_URL", "https://api-inference.modelscope.cn/v1"))
    # 思考模式开关 → 请求体参数格式
    thinking = bool(pick("thinking", "MODELSCOPE_THINKING", True))
    extra_request = {"thinking": {"type": "enabled" if thinking else "disabled"}}
    return main(messages, model_name=model, api_key=api_key, temperature=temperature,
                base_url=base_url, extra_body=extra_request)

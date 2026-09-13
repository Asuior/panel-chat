# -*- coding: utf-8 -*-
"""OpenAI 兼容接口 —— 可接入任意 OpenAI Chat Completions 形态的服务。

与内置的 ``deepseek`` / ``ModelScope`` 两个插件结构相同（同一个 LangGraph
Agent 循环），区别在客户端：那两个用 ``ChatDeepSeek`` 以支持思维链解析，
本插件改用通用的 ``langchain_openai.ChatOpenAI``，因此不绑定任何厂商的
专属行为，适配官方 OpenAI、vLLM、Ollama、OpenRouter、各类中转等端点。

思考模式等厂商专有参数不占用固定字段，而是由用户在
「设置 → AI 接口」的参数表单里直接填写 ``extra_body``（JSON）：
内容原样透传，最终出现在请求体顶层。例如：

    {"thinking": {"type": "enabled"}}      DeepSeek 系
    {"reasoning_effort": "high"}           官方 OpenAI 推理模型
    {"thinking": {"type": "disabled"}}

填写非法 JSON 时会阻止本次请求并提示，避免"以为生效其实没生效"。
"""
from __future__ import annotations

import json
import os
from typing import Any

from core.plugin_manager import register_ai_provider, load_plugin_config, coerce_params
from .main import main

_CONFIG = load_plugin_config(__file__)

PARAMS_SCHEMA: dict[str, dict[str, Any]] = {
    "api_key": {
        "type": "string",
        "name": "API Key（接口秘钥）",
        "description": "目标服务的密钥；本地 vLLM / Ollama 等不校验的可随便填。敏感字段，仅保存在本机。",
        "sensitive": True,
        "default": "",
    },
    "model": {
        "type": "string",
        "name": "模型名称",
        "description": "目标服务支持的模型 id，例如 gpt-4o-mini / Qwen/Qwen3-8B",
        "default": "gpt-4o-mini",
    },
    "base_url": {
        "type": "string",
        "name": "Base URL",
        "description": "OpenAI 兼容端点，通常以 /v1 结尾，例如 https://api.openai.com/v1",
        "default": "https://api.openai.com/v1",
    },
    "extra_body": {
        "type": "json",
        "name": "extra_body（请求体附加参数）",
        "description": (
            "可选。这里填写的 JSON 会原样合并进请求体顶层，用来传递厂商专有参数"
        ),
        "placeholder": '{"reasoning_effort": "high"}',
        "default": "",
    },
}


def _parse_extra_body(raw: Any) -> dict:
    """
    解析用户填写的 extra_body。

    留空 → 空 dict（不附加任何参数）；
    非空但非法 → 抛 RuntimeError，阻止本次请求（前端会弹 toast 提示）。
    这里刻意不"静默忽略"：否则用户会以为思考模式已生效，实际没生效，
    这种问题极难排查。
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    text = str(raw).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise RuntimeError(
            f"extra_body 不是合法的 JSON，本次请求已取消：{exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(
            "extra_body 必须是一个 JSON 对象（以 { } 包裹），本次请求已取消。"
        )
    return parsed


@register_ai_provider(
    provider_id="openai_compatible",
    name="OpenAI 兼容接口",
    description="接入任意 OpenAI Chat Completions 兼容服务（官方 OpenAI / vLLM / Ollama / 中转等）",
    params=PARAMS_SCHEMA,
    supports_images=False,
)
def chat(messages: list, temperature: float = 0.7, extra_body: dict | None = None) -> str:
    """
    调用 OpenAI 兼容 Agent 完成一轮对话（内部可能包含多次工具调用）。

    :param messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
    :param temperature: 采样温度
    :param extra_body: 用户在设置页填写的自定义参数
        （api_key/model/base_url/extra_body）
    :return: 纯文本回复
    """
    extra = coerce_params(extra_body, PARAMS_SCHEMA)

    def pick(key: str, env_name: str, default: Any):
        """extra_body > 环境变量 > config.json > default

        只有 None 与空字符串算"用户没填"；False / 0 都是显式取值。
        """
        val = extra.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            val = os.environ.get(env_name)
            if val is None or (isinstance(val, str) and not val.strip()):
                val = _CONFIG.get(key, default)
        return val

    api_key = str(pick("api_key", "OPENAI_API_KEY", "") or "").strip()
    model = str(pick("model", "OPENAI_MODEL", "gpt-4o-mini"))
    base_url = str(pick("base_url", "OPENAI_BASE_URL", "https://api.openai.com/v1"))
    # 非法 JSON 会抛 RuntimeError，由 core.plugin_manager 记录并传递给前端
    request_extra = _parse_extra_body(pick("extra_body", "OPENAI_EXTRA_BODY", ""))
    return main(messages, model_name=model, api_key=api_key, temperature=temperature,
                base_url=base_url, extra_body=request_extra)

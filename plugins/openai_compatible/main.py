"""OpenAI 兼容接口的 LangGraph Agent（对话 ↔ 工具循环）。

与 plugins/deepseek、plugins/ModelScope 的循环结构一致，区别在**客户端选择**：

    * 那两个插件用 ``langchain_deepseek.ChatDeepSeek``，因为它会把响应里的
      ``reasoning_content``（思维链）解析到 ``additional_kwargs``，且会针对
      DeepSeek 的接口形态修正请求体（如 assistant content 必须是字符串）。
    * 本插件要服务于**任意** OpenAI 兼容端点（官方 OpenAI、vLLM、Ollama、
      OpenRouter、各类中转等），因此不应绑定 DeepSeek 的专属行为，改用通用的
      ``langchain_openai.ChatOpenAI``。

思考模式等厂商专有参数不走 PARAMS_SCHEMA 的固定字段，而是由用户在
「设置 → AI 接口」里直接填写 ``extra_body``（JSON），原样透传到请求体顶层，
例如 ``{"thinking": {"type": "enabled"}}`` 或 ``{"reasoning_effort": "high"}``。
"""
from __future__ import annotations

import os
from typing import Annotated, Any

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import add_messages
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict

from plugins.openai_compatible.skill_loader import load_skill as get_skill
from plugins.openai_compatible.tools.loader import import_tools
from plugins.openai_compatible.tools.tools_98es7d5.skill_tools import list_skills, load_skill


class GraphState(TypedDict):
    """State carried through the graph.

    ``messages`` uses ``add_messages`` so each node *appends* to the list
    rather than replacing it.

    ``active_skill`` 记录当前激活的技能名;为空表示尚未加载技能。

    ``extra_body`` 由用户直接填写的请求体附加参数(JSON),原样透传给
    ChatOpenAI,用于表达厂商专有开关(thinking / reasoning_effort 等)。
    """
    messages: Annotated[list[BaseMessage], add_messages]
    final_reply: BaseMessage
    active_skill: str | None
    model_name: str | None
    api_key: str | None
    temperature: float | None
    base_url: str | None
    extra_body: dict | None


# 技能管理工具始终绑定给模型,用于「发现 / 激活」技能
SKILL_TOOLS = [list_skills, load_skill]

# 工具在模块加载时导入一次即可:注册表是模块级字典,import_tools() 每次都会
# glob 整个 tools*/ 目录并重新 import。放在节点内部会在 Agent 的每一轮往返里
# 重复执行(一轮对话可能调用 chat_node 多次)。
_PERMANENT_TOOLS = import_tools("permanent")
_DEMAND_TOOLS = import_tools("demand")
# ToolNode 需要持有全部工具的引用,才能执行模型选中的任何一个
_ALL_TOOLS = list({**_DEMAND_TOOLS, **_PERMANENT_TOOLS}.values()) + SKILL_TOOLS


def chat_node(state: GraphState) -> dict[str, Any]:
    active_name = state.get("active_skill")
    model_name = state.get("model_name")
    api_key = state.get("api_key")
    temperature = state.get("temperature")
    base_url = state.get("base_url")
    extra_body = dict(state.get("extra_body") or {})
    skill = get_skill(active_name) if active_name else None

    messages = list(state["messages"])
    if skill:
        # 注入技能执行规范 + 业务流程,作为 system 提示词
        messages = [SystemMessage(content=skill.build_prompt())] + messages
        # 仅绑定该技能允许使用的工具,加上技能管理工具
        tools = list(_PERMANENT_TOOLS.values()) + [_DEMAND_TOOLS[name] for name in skill.tools if
                                                   name in _DEMAND_TOOLS] + SKILL_TOOLS
    else:
        tools = list(_PERMANENT_TOOLS.values()) + SKILL_TOOLS

    # 官方 OpenAI 的推理模型(o 系列/gpt-5 等)会直接拒绝 temperature;
    # 若用户显式配置了思考相关参数,就说明目标端点很可能是这类模型,
    # 此时把 temperature 去掉,避免整个请求被 400 打回。
    # 非推理模型不填 extra_body,行为与其它插件保持一致(照常带 temperature)。
    if extra_body.get("thinking") is not None or extra_body.get("reasoning_effort") is not None:
        temperature = None

    llm = ChatOpenAI(
        model=model_name,
        temperature=temperature,
        api_key=api_key or "not-needed",  # vLLM / Ollama 等本地端点通常不校验
        base_url=base_url,
        # 透传请求体附加参数;为空时保持库的默认行为
        extra_body=extra_body or None,
    ).bind_tools(tools)

    ai_msg = llm.invoke(messages)
    return {"messages": [ai_msg], "final_reply": ai_msg}


def main(messages, model_name="gpt-4o-mini", api_key=None, temperature=0,
         base_url="https://api.openai.com/v1", extra_body=None):
    tool_node = ToolNode(tools=_ALL_TOOLS)

    graph = StateGraph(GraphState)
    graph.add_node("chat", chat_node)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "chat")
    graph.add_conditional_edges("chat", tools_condition)
    graph.add_edge("tools", "chat")
    graph = graph.compile()

    result = graph.invoke(
        {"messages": messages, "model_name": model_name, "api_key": api_key, "temperature": temperature,
         "base_url": base_url, "extra_body": extra_body or {}})
    return result.get("final_reply", {}).content


if __name__ == "__main__":
    main([{"role": "user", "content": "你好,请去查一下今天北京的天气"}])

import os
from typing import Annotated, Any

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from langgraph.graph import add_messages
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict

from plugins.deepseek.skill_loader import load_skill as get_skill
from plugins.deepseek.tools.loader import import_tools
from plugins.deepseek.tools.tools_98es7d5.skill_tools import list_skills, load_skill


class GraphState(TypedDict):
    """State carried through the graph.

    ``messages`` uses ``add_messages`` so each node *appends* to the list
    rather than replacing it.

    ``active_skill`` 记录当前激活的技能名;为空表示尚未加载技能。
    """
    messages: Annotated[list[BaseMessage], add_messages]
    final_reply: BaseMessage
    active_skill: str | None
    model_name: str | None
    api_key: str | None
    temperature: float | None
    base_url: str | None


# 技能管理工具始终绑定给模型,用于「发现 / 激活」技能
SKILL_TOOLS = [list_skills, load_skill]


def chat_node(state: GraphState) -> dict[str, Any]:
    base_tools = import_tools("permanent")  # 注册的基础工具(name -> tool)

    active_name = state.get("active_skill")
    model_name = state.get("model_name")
    api_key = state.get("api_key")
    temperature = state.get("temperature")
    base_url = state.get("base_url")
    os.environ["DEEPSEEK_API_BASE"] = base_url
    skill = get_skill(active_name) if active_name else None

    messages = list(state["messages"])
    if skill:
        # 注入技能执行规范 + 业务流程,作为 system 提示词
        messages = [SystemMessage(content=skill.build_prompt())] + messages
        # 仅绑定该技能允许使用的工具,加上技能管理工具
        base_demand_tools = import_tools("demand")
        tools = list(base_tools.values()) + [base_demand_tools[name] for name in skill.tools if
                                             name in base_demand_tools] + SKILL_TOOLS
    else:
        tools = list(base_tools.values()) + SKILL_TOOLS

    llm = ChatDeepSeek(
        model=model_name,  # 或 "deepseek-reasoner"
        temperature=temperature,
        max_tokens=None,
        api_key=api_key
    ).bind_tools(tools)

    ai_msg = llm.invoke(messages)
    return {"messages": [ai_msg], "final_reply": ai_msg}


def main(messages, model_name="deepseek-v4-flash", api_key=None, temperature=0,
         base_url="https://api.deepseek.com/v1"):
    # 全部工具 = 注册工具 + 技能管理工具
    tools = list(import_tools("all").values()) + SKILL_TOOLS
    tool_node = ToolNode(tools=tools)

    graph = StateGraph(GraphState)
    graph.add_node("chat", chat_node)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "chat")
    graph.add_conditional_edges("chat", tools_condition)
    graph.add_edge("tools", "chat")
    graph = graph.compile()

    result = graph.invoke(
        {"messages": messages, "model_name": model_name, "api_key": api_key, "temperature": temperature,
         "base_url": base_url})
    return result.get("final_reply", {}).content


if __name__ == "__main__":
    main([{"role": "user", "content": "你好,请去查一下今天北京的天气"}])

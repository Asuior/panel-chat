"""技能管理的 LangChain 工具:让模型可以「发现」并「激活」技能。

- ``list_skills``:列出当前所有可用技能及说明,供模型决定是否加载。
- ``load_skill``: 加载并激活指定技能,使其执行规范与可用工具在后续轮次生效。

这两个工具始终绑定给模型,不通过 ``tool_resigter`` 注册(它们是标准
LangChain ``@tool``,避免与技能管理工具重复出现在工具表里)。
"""
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from plugins.ModelScope.skill_loader import list_skills as _list_skills
from plugins.ModelScope.skill_loader  import load_skill as _load_skill


@tool
def list_skills() -> str:
    """列出当前所有可用技能及其说明,用于决定是否加载某项技能。"""
    skills = _list_skills()
    if not skills:
        return "当前没有任何可用的技能。"
    lines = [f"- {s.name}: {s.description}" for s in skills]
    return "可用的技能如下:\n" + "\n".join(lines)


@tool
def load_skill(
    skill_name: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """加载并激活指定技能,使其执行规范与可用工具在后续对话轮次生效。"""
    try:
        skill = _load_skill(skill_name)
    except ValueError as exc:
        return Command(
            update={"messages": [ToolMessage(content=str(exc), tool_call_id=tool_call_id)]}
        )

    active_tools = ", ".join(skill.tools) if skill.tools else "无"
    flow_text = " -> ".join(skill.flow) if skill.flow else "无"
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=(
                        f"已加载技能【{skill.name}】:{skill.description}\n"
                        f"该技能可使用的工具: {active_tools}\n"
                        f"业务流程: {flow_text}"
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
            "active_skill": skill.name,
        }
    )

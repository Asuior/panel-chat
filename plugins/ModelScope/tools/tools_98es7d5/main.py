import json

from langchain_core.tools import InjectedToolCallId
from langgraph.prebuilt import InjectedState

from plugins.deepseek.tools.loader import tool_resigter
from langchain_core.messages import AIMessage, BaseMessage, RemoveMessage, ToolMessage
from langgraph.types import RunnableConfig
from langchain_openai import ChatOpenAI
from typing import Any
from langgraph.types import Command  # 👈 引入 Command
from typing_extensions import Annotated


@tool_resigter("get_weather","demand")
def get_weather() -> str:
    """
    当用户想要得知北京市天气时，调用此函数获取
    :return: weather:str
    """
    return "晴，32℃"

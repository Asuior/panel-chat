# -*- coding: utf-8 -*-
"""
AI 接口插件化系统。

设计目标：主程序与具体 AI 接口完全解耦。

插件规范（plugins/<name>/__init__.py）：
    from core.plugin_manager import register_ai_provider, load_plugin_config

    @register_ai_provider(
        provider_id="openai_compatible",
        name="OpenAI 兼容接口",
        description="...",
    )
    def chat(messages: list, temperature: float = 0.7) -> str:
        # messages: [{"role": "system"|"user"|"assistant", "content": "..."}, ...]
        # 返回纯文本回复
        ...

    # 可选：加载同目录 config.json
    cfg = load_plugin_config(__file__)

说明：
    * 每个插件一个子目录；程序启动时自动扫描 plugins/ 并加载；
    * API Key / Base URL / 模型名由插件自己的 config.json 或环境变量管理；
    * 主程序通过 provider_id 路由到对应插件的 chat()；
    * 注册表线程安全。
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from core.logger import get_logger

log = get_logger(__name__)

_registry_lock = threading.RLock()
# provider_id -> {"id","name","description","chat", "module"}
_registry: dict[str, dict[str, Any]] = {}


# --------------------------------------------------------------------------- #
# 注册器
# --------------------------------------------------------------------------- #
def register_ai_provider(
    provider_id: str,
    name: str,
    description: str = "",
    params: dict[str, dict[str, Any]] | None = None,
    supports_images: bool = False,
    welcome_html: str = "",
):
    """
    装饰器：把一个函数注册为 AI provider。

    :param params: 自定义参数描述字典（可选），例如
        {"api_key": {"type": "string", "name": "接口秘钥", "description": "...",
                     "default": "", "sensitive": True}}
        前端会依据该字典在“设置 → AI 接口”页渲染参数表单；
        用户填写的值会被收集为 dict 以 extra_body 参数传入 chat()。
    :param supports_images: 该接口是否支持多模态图片输入（声明能力）。
        开启后，前端输入框允许拖拽/粘贴/上传图片，并以如下格式构造消息：
            {"role":"user","content":[
                {"type":"text","text":"..."},
                {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}
            ]}
    :param welcome_html: 可选；该接口专属的“空会话欢迎页”HTML 片段。
        · 生效范围：只在当前会话没有任何消息时显示，该替换只作用于欢迎页内容
          （外层 .welcome 容器保留，因此自动沿用居中布局与主题变量；
          自定义时容器额外带 .custom，可写 .welcome.custom 单独定制）；
        · **不含头像位**：欢迎页顶部的 .big 头像由程序渲染、跟随
          “设置 → 外观”的 AI 头像，不属于本参数的范围，请不要自己写 .big；
        · 回落规则：留空（默认）则该接口继续使用内置默认欢迎词；
        · 切换行为：无论从托盘菜单、聊天窗头部下拉还是“设置 → AI 接口”切换，
          欢迎页都会随之原地替换；已有消息的会话不受影响
          （不重绘消息、不重置滚动位置）；
        · 可交互：片段里带 data-tab="ai|prompts|..." 的元素会自动绑定为
          “跳转到对应设置页签”的按钮；
        · 安全：片段以 innerHTML 原样注入本地窗口（<script> 不执行，但内联
          事件属性如 onclick 会生效）。插件本就是本机可执行代码，不构成额外
          的信任边界，但请勿拼接来自网络或用户输入的内容。
    """
    def decorator(func: Callable) -> Callable:
        with _registry_lock:
            _registry[provider_id] = {
                "id": provider_id,
                "name": name,
                "description": description,
                "params": dict(params or {}),
                "supports_images": bool(supports_images),
                # 统一去掉首尾空白：空串 / 纯空白都表示“该接口没有自定义欢迎词”
                "welcome_html": str(welcome_html or "").strip(),
                "chat": func,
                "module": func.__module__,
            }
        log.info("AI 插件已注册: %s (%s)", provider_id, name)
        return func

    return decorator


def load_plugin_config(plugin_file: str | os.PathLike) -> dict:
    """
    读取插件目录下的 config.json（若存在）。供插件内部调用。

    :param plugin_file: 插件 __init__.py 的路径，即 __file__
    """
    cfg_path = Path(plugin_file).resolve().parent / "config.json"
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("读取插件配置失败 %s: %s", cfg_path, exc)
        return {}


def load_plugin_from_path(path: Path, plugin_root: Path) -> bool:
    """加载单个插件目录。"""
    init_file = path / "__init__.py"
    if not init_file.exists():
        return False
    try:
        # 以 plugins.<目录名> 为模块名加载，保证可重复导入
        module_name = f"plugins.{path.name}"
        spec = importlib.util.spec_from_file_location(module_name, init_file)
        if spec is None or spec.loader is None:
            return False
        module = importlib.util.module_from_spec(spec)
        # 设置 __package__ 以便相对导入（同目录辅助模块）
        module.__package__ = module_name
        module.__path__ = [str(path)]
        spec.loader.exec_module(module)
        log.info("插件目录已加载: %s", path.name)
        return True
    except Exception:
        log.exception("插件加载失败: %s", path)
        return False


class PluginManager:
    """扫描并加载 plugins/ 目录下所有插件。"""

    def __init__(self, plugin_dir: str | os.PathLike | None = None):
        self._plugin_dir = Path(plugin_dir) if plugin_dir else Path(
            __file__).resolve().parent.parent / "plugins"
        self._loaded: list[str] = []

    # ------------------------------------------------------------------ #
    def load_plugins(self) -> list[str]:
        """加载所有插件子目录，返回成功加载的目录名列表。"""
        self._plugin_dir.mkdir(parents=True, exist_ok=True)
        for entry in sorted(self._plugin_dir.iterdir()):
            if entry.is_dir() and not entry.name.startswith((".", "__")):
                if load_plugin_from_path(entry, self._plugin_dir):
                    self._loaded.append(entry.name)
        log.info("插件扫描完成，共加载 %d 个: %s", len(self._loaded), self._loaded)
        return list(self._loaded)

    # ------------------------------------------------------------------ #
    def get_providers(self) -> list[dict]:
        """返回全部已注册 provider 列表（含参数描述、图片能力与欢迎页声明）。"""
        with _registry_lock:
            return [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "description": p.get("description", ""),
                    "params": p.get("params", {}),
                    "supports_images": p.get("supports_images", False),
                    "welcome_html": p.get("welcome_html", "") or "",
                }
                for p in _registry.values()
            ]

    def get_provider(self, provider_id: str) -> dict | None:
        with _registry_lock:
            p = _registry.get(provider_id)
            return p

    def chat(
        self,
        provider_id: str,
        messages: list,
        temperature: float = 0.7,
        extra_body: dict | None = None,
    ) -> str:
        """
        路由到对应插件的 chat() 执行。

        :param extra_body: 用户在接口设置页填写的自定义参数字典；
            优先以 chat(messages, temperature, extra_body) 调用；
            若插件只声明了 2 个参数（旧插件），自动回退为不带 extra_body 调用。

        :raises KeyError: provider 不存在
        """
        provider = self.get_provider(provider_id)
        if not provider:
            raise KeyError(
                f"AI 接口 '{provider_id}' 未注册。请在“设置 → AI 接口”中选择可用接口。"
            )
        chat_func = provider["chat"]
        if temperature is None:
            temperature = 0.7
        extra = dict(extra_body) if isinstance(extra_body, dict) else {}
        try:
            log.info("调用 AI 接口 %s (temperature=%.2f, 消息数=%d)",
                     provider_id, temperature, len(messages or []))
            try:
                result = chat_func(messages or [], float(temperature), extra)
            except TypeError:
                # 旧插件：chat(messages, temperature)
                result = chat_func(messages or [], float(temperature))
            if not isinstance(result, str):
                result = str(result)
            return result
        except Exception:
            log.exception("AI 接口 %s 调用失败", provider_id)
            raise

# -*- coding: utf-8 -*-
"""
全局配置管理（settings.json）。

职责：
    * 首次运行时以默认值创建 data/settings.json；
    * 提供线程安全的 get / update / save；
    * 记忆悬浮球 / 悬浮窗位置、所选 AI 接口、主题、用户名/头像等。

所有字段均有默认值，新增字段不会导致旧文件读取失败（做深合并）。
"""
from __future__ import annotations

import json
import threading
from typing import Any

from core.logger import get_logger
from core.paths import SETTINGS_FILE

log = get_logger(__name__)

# 默认配置。结构与 data/settings.json 保持一致。
DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "sidebar_visible": True,             # 聊天窗左侧会话栏是否展开
    # ---- 窗口 ----
    "ball": {
        "size": 70,                     # 悬浮球直径（px）
        "x": None,                      # None = 使用默认位置（屏幕右下）
        "y": None,
    },
    # 悬浮窗：始终置顶，无“取消置顶”开关（工具窗口没有任务栏入口，
    # 一旦取消置顶 + 显示桌面就找不到窗口了）
    "chat": {
        "width": 420,                   # 默认 420x700（规格说明）
        "height": 700,
        "min_width": 500,               # 规格：最小 500x600
        "min_height": 600,
        "x": None,
        "y": None,
    },
    # ---- 全局热键（keyboard 库语法）----
    "hotkeys": {
        "toggle_ball": "ctrl+alt+q",
        "toggle_chat": "ctrl+alt+w",
    },
    # ---- AI 接口 ----
    "provider": "mock",                 # 当前选中的 provider id
    "temperature": 0.7,
    "provider_params": {},              # 各接口的自定义参数 {provider_id: {param: value}}
    "multimodal": {},                   # 图片输入开关覆盖 {provider_id: bool}（缺省=插件声明）
    # ---- 提示词：已启用分组（按顺序组合）----
    "selected_prompt_groups": [],       # group id 列表（有序）
    # ---- UI 自定义 ----
    "theme": "glassmorphism",
    "background_image": None,           # 背景图：web/assets/backgrounds/xxx.png 或绝对路径
    "use_background_blur": True,
    "user": {"name": "我", "avatar": None},   # avatar: "default" 或 data url / 文件 url
    "assistant": {"name": "AI 助手", "avatar": None},
    # ---- 悬浮球 ----
    "ball_light_effect": True,          # 按时段自动切换的光效
    # ---- 文件处理 ----
    "file_processing": {
        "mode": "overwrite",            # overwrite | append
        "instruction": (
            "请阅读下面提供的文件内容，直接输出优化后的完整文件内容。"
            "不要添加额外解释，不要使用 Markdown 代码块包裹，保持原格式。"
        ),
        "max_chars": 80000,             # 超过此字符数拒绝读取，避免撑爆上下文
    },
    # ---- 其它 ----
    "auto_launch": False,               # 开机自启（预留，后续实现）
}


def _deep_merge(base: dict, patch: dict) -> dict:
    """把 patch 深合并到 base（返回新 dict，不修改入参）。"""
    out = dict(base)
    for key, value in patch.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class SettingsManager:
    """settings.json 的读写封装（线程安全）。"""

    def __init__(self, file_path=None):
        self._file = file_path or SETTINGS_FILE
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self.load()

    # ---------- 读取 ----------
    def load(self) -> dict:
        """从磁盘加载配置，并与默认值合并。"""
        with self._lock:
            raw = {}
            if self._file.exists():
                try:
                    raw = json.loads(self._file.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    log.warning("settings.json 解析失败，使用默认配置: %s", exc)
            self._data = _deep_merge(DEFAULT_SETTINGS, raw)
            return self._data

    def get(self, key: str, default: Any = None) -> Any:
        """点分路径取值，如 get('chat.width')。"""
        with self._lock:
            node: Any = self._data
            for part in key.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    return default
            return node

    def all(self) -> dict:
        """返回完整配置（深拷贝，避免外部篡改内部状态）。"""
        with self._lock:
            return json.loads(json.dumps(self._data))

    # ---------- 写入 ----------
    def set(self, key: str, value: Any) -> None:
        """点分路径写入并立即落盘。"""
        with self._lock:
            parts = key.split(".")
            node = self._data
            for part in parts[:-1]:
                nxt = node.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    node[part] = nxt
                node = nxt
            node[parts[-1]] = value
            self.save()

    def update(self, patch: dict) -> None:
        """以深合并方式整体更新并落盘。"""
        with self._lock:
            self._data = _deep_merge(self._data, patch)
            self.save()

    def save(self) -> None:
        with self._lock:
            try:
                self._file.parent.mkdir(parents=True, exist_ok=True)
                self._file.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                log.error("写入 settings.json 失败: %s", exc)

    # ---------- 便捷方法 ----------
    def remember_window_position(self, window_id: str, x: int, y: int) -> None:
        """记忆窗口位置（window_id: ball | chat）。"""
        if window_id in ("ball", "chat"):
            self.set(f"{window_id}.x", int(x))
            self.set(f"{window_id}.y", int(y))

    def remember_window_geometry(self, window_id: str, x: int, y: int,
                                 width: int | None = None,
                                 height: int | None = None) -> None:
        """
        记忆窗口几何：位置总是记录；chat 窗口尺寸可调整，额外记录宽高。
        （悬浮球尺寸由 ball.size 决定，不记录宽高。）
        合并成一次写入，只落盘一次。
        """
        if window_id not in ("ball", "chat"):
            return
        node: dict[str, Any] = {"x": int(x), "y": int(y)}
        if window_id == "chat" and width and height:
            node["width"] = int(width)
            node["height"] = int(height)
        self.update({window_id: node})

    def window_position(self, window_id: str) -> tuple[int | None, int | None]:
        """读取记忆的窗口位置。"""
        return self.get(f"{window_id}.x"), self.get(f"{window_id}.y")

    def window_size(self, window_id: str) -> tuple[int | None, int | None]:
        """读取记忆的窗口尺寸（仅 chat 有实际意义）。"""
        return self.get(f"{window_id}.width"), self.get(f"{window_id}.height")

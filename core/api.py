# -*- coding: utf-8 -*-
"""
前后端桥接 API（作为 pywebview 的 js_api 注入两个窗口）。

前端统一通过 window.pywebview.api.<函数名>(...) 调用；
所有函数执行于 pywebview 工作线程（不阻塞 UI）。

约定：
    * 返回 JSON 可序列化对象；
    * 业务错误抛 RuntimeError（中文），前端 promise 会以 rejection 收到
      {message: ...} 并转成 toast。
"""
from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from core.chat_payload import build_chat_payload, summary as payload_summary  # 发送前的消息过滤/组装
from core.logger import get_logger
from core.paths import THEMES_DIR, WEB_DIR
from core.plugin_manager import PluginManager
from core.prompt_manager import PromptManager
from core.settings_manager import SettingsManager

log = get_logger(__name__)


def _file_url(path: Path) -> str:
    return path.resolve().as_uri()


class Api:
    """
    暴露给前端的全部方法。

    :param settings: SettingsManager
    :param prompts: PromptManager
    :param conversations: ConversationManager
    :param plugins: PluginManager
    :param window_ops: 可选；提供 show/hide/move/... 的可调用对象（由 main 注入）
    """

    def __init__(
        self,
        settings: SettingsManager,
        prompts: PromptManager,
        conversations,
        plugins: PluginManager,
        window_ops: Any = None,
    ):
        self._settings = settings
        self._prompts = prompts
        self._conversations = conversations
        self._plugins = plugins
        self._win = window_ops  # WindowController | None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    def _require_window(self):
        if self._win is None:
            raise RuntimeError("窗口控制器不可用（非 GUI 环境）")
        return self._win

    # ------------------------------------------------------------------ #
    # 1) AI 接口插件
    # ------------------------------------------------------------------ #
    def get_providers(self) -> list[dict]:
        """
        返回所有已注册的 AI 接口列表。每个 provider 额外带：
          · multimodal_supported —— 插件声明的图片能力；
          · multimodal —— 当前是否“允许图片输入”（用户可在设置里覆盖关闭）；
          · welcome_html —— 插件声明的自定义欢迎页 HTML（空串 = 用内置默认）。
        """
        providers = self._plugins.get_providers()
        overrides = self._settings.get("multimodal") or {}
        # 把已失效的 provider 配置纠正回第一个可用项
        current = self._settings.get("provider")
        if current not in {p["id"] for p in providers}:
            if providers:
                self._settings.set("provider", providers[0]["id"])
        enriched = []
        for p in providers:
            supported = bool(p.get("supports_images", False))
            override = overrides.get(p["id"])
            effective = override if isinstance(override, bool) else supported
            enriched.append({
                "id": p["id"],
                "name": p["name"],
                "description": p.get("description", ""),
                "params": p.get("params", {}),
                "multimodal_supported": supported,
                "multimodal": effective,
                # 空串 / 缺失都表示“该接口没有自定义欢迎词”，前端回落到内置默认
                "welcome_html": p.get("welcome_html", "") or "",
            })
        return enriched

    def set_provider_multimodal(self, provider_id: str, enabled: bool) -> bool:
        """设置某个接口是否允许图片输入（仅对声明支持图片的接口有意义）。"""
        overrides = dict(self._settings.get("multimodal") or {})
        overrides[provider_id] = bool(enabled)
        self._settings.set("multimodal", overrides)
        return True

    def _multimodal_enabled(self, provider_id: str) -> bool:
        """当前接口是否允许图片输入（插件能力 + 用户开关）。"""
        provider = self._plugins.get_provider(provider_id) or {}
        supported = bool(provider.get("supports_images", False))
        override = (self._settings.get("multimodal") or {}).get(provider_id)
        return override if isinstance(override, bool) else supported

    def _extra_body(self, provider_id: str) -> dict:
        """
        计算传给插件 chat() 的 extra_body。

        规则：
          · 用户在设置页手动保存过的值优先（provider_params[provider_id]）；
          · 从未被修改（存储中缺失）但插件声明了 default 的参数，
            自动补上其默认值 —— 保证“没动过的默认参数”也会随 extra_body 传出；
          · 用户显式清空（存了空字符串）的保持空串，不改写，
            让插件内部的环境变量/config.json 回退链继续生效。
        """
        stored_raw = (self._settings.get("provider_params") or {}).get(provider_id)
        stored = dict(stored_raw) if isinstance(stored_raw, dict) else {}

        provider = self._plugins.get_provider(provider_id)
        schema = (provider or {}).get("params") or {}
        for key, spec in schema.items():
            if key not in stored and isinstance(spec, dict):
                default = spec.get("default")
                if default is not None:
                    stored[key] = default
        return stored

    def chat(self, provider_id: str, messages: list, temperature: float = 0.7) -> str:
        """
        发送对话请求（messages 含 role/content，可携带 images），返回 AI 回复文本。

        发送前统一过滤（见 core/chat_payload.build_chat_payload）：
            · 只把 messages 中“最后一条用户消息”的图片组装成多模态 parts；
            · 历史消息里的图片一律丢弃，只保留文本，避免占用上下文；
            · 接口未开启多模态时，所有图片都被丢弃；
            · 只输出 role/content，本地字段（timestamp/images）不外传。

        注意：本地对话记录里仍完整保留图片，前端用消息里的 images
        字段渲染（这是展示用的，与本次请求无关）。
        """
        if not provider_id:
            provider_id = self._settings.get("provider") or "mock"
        # 记住用户的选择
        self._settings.set("provider", provider_id)
        payload = build_chat_payload(
            messages,
            allow_images=self._multimodal_enabled(provider_id),
            resolver=getattr(self._conversations, "entry_to_data_url", None),
        )
        stats = payload_summary(payload)
        if stats["images"]:
            log.info(
                "多模态请求：本次携带 %d 张图片（历史图片已过滤，消息数=%d）",
                stats["images"], stats["messages"],
            )
        return self._plugins.chat(
            provider_id, payload, temperature, extra_body=self._extra_body(provider_id)
        )

    # ------------------------------------------------------------------ #
    # 2) 会话管理
    # ------------------------------------------------------------------ #
    def get_conversations(self) -> list[dict]:
        """返回历史会话索引（id, title, preview, updated_at），倒序。"""
        return self._conversations.list_conversations()

    def get_conversation(self, conv_id: str) -> dict:
        """返回指定会话全部消息。"""
        conv = self._conversations.get_conversation(conv_id)
        if conv is None:
            raise RuntimeError("会话不存在或已被删除。")
        return conv

    def get_image_data_url(self, rel_path: str) -> str:
        """
        把对话图片（消息里的相对路径）读成 base64 data URL。

        正常渲染走相对路径与页面同源，无需经过这里；
        这是 <img> 加载失败（如运行环境限制本地子资源）时的兜底通道。
        """
        getter = getattr(self._conversations, "entry_to_data_url", None)
        url = getter({"file": rel_path}) if getter else None
        if not url:
            raise RuntimeError("图片不存在或无法读取。")
        return url

    def save_conversation(self, conv_id: str, messages: list) -> dict:
        """保存会话全部消息（自动维护索引与标题）。"""
        return self._conversations.save_conversation(conv_id, messages)

    def rename_conversation(self, conv_id: str, title: str) -> bool:
        return self._conversations.rename_conversation(conv_id, title)

    def delete_conversation(self, conv_id: str) -> bool:
        return self._conversations.delete_conversation(conv_id)

    def delete_message(self, conv_id: str, msg_index: int) -> dict | None:
        """删除指定消息及其后所有消息（分支截断）。"""
        return self._conversations.delete_from(conv_id, int(msg_index))

    def update_message(self, conv_id: str, msg_index: int, new_content: str) -> dict | None:
        """修改消息内容（本地编辑）。"""
        return self._conversations.update_message(conv_id, int(msg_index), new_content)

    # ------------------------------------------------------------------ #
    # 3) 系统提示词
    # ------------------------------------------------------------------ #
    def get_prompts(self) -> dict:
        """返回 {groups: [...]}。"""
        return {"groups": self._prompts.get_groups()}

    def save_prompts(self, prompts_data: Any) -> bool:
        """整体保存提示词分组（兼容 {groups:[...]} 或 裸列表）。"""
        if isinstance(prompts_data, dict):
            groups = prompts_data.get("groups", [])
        else:
            groups = prompts_data or []
        self._prompts.replace_all(list(groups))
        return True

    def compose_system_prompt(self) -> str:
        """按已启用分组（有序）组合系统提示词。供内部/调试使用。"""
        ids = self._settings.get("selected_prompt_groups") or []
        return self._prompts.compose(ids)

    def set_selected_prompt_groups(self, group_ids: list) -> bool:
        """设置启用的提示词分组顺序。"""
        self._settings.set("selected_prompt_groups", list(group_ids or []))
        return True

    # ------------------------------------------------------------------ #
    # 4) UI 自定义 / 设置
    # ------------------------------------------------------------------ #
    def get_settings(self) -> dict:
        return self._settings.all()

    def save_settings(self, settings: dict) -> dict:
        """整体合并保存 UI 自定义配置。"""
        self._settings.update(settings or {})
        return self._settings.all()

    def get_themes(self) -> list[dict]:
        """
        扫描 themes/ 目录，返回可用主题列表。
        每个主题文件夹需含 theme.css 与 manifest.json。
        """
        themes: list[dict] = []
        if THEMES_DIR.exists():
            for folder in sorted(THEMES_DIR.iterdir()):
                if not folder.is_dir() or folder.name.startswith((".", "__")):
                    continue
                css = folder / "theme.css"
                manifest = folder / "manifest.json"
                if not css.exists():
                    continue
                meta = {}
                if manifest.exists():
                    try:
                        meta = json.loads(manifest.read_text(encoding="utf-8"))
                    except (OSError, ValueError) as exc:
                        log.warning("主题 manifest 解析失败 %s: %s", folder.name, exc)
                themes.append(
                    {
                        "id": folder.name,
                        "name": meta.get("name") or folder.name,
                        "author": meta.get("author", ""),
                        "description": meta.get("description", ""),
                        "css_url": _file_url(css),
                        "manifest_url": _file_url(manifest) if manifest.exists() else None,
                    }
                )
        return themes

    def apply_theme(self, theme_name: str) -> dict:
        """应用主题：记录到设置并返回该主题 css_url 供前端加载。"""
        themes = {t["id"]: t for t in self.get_themes()}
        theme = themes.get(theme_name)
        if not theme:
            raise RuntimeError(f"主题「{theme_name}」不存在。")
        self._settings.set("theme", theme_name)
        return theme

    # -- 背景图 / 头像上传 --------------------------------------------- #
    def upload_background(self, filename: str, data_url: str) -> str:
        """
        保存用户上传的背景图到 web/assets/backgrounds/，
        返回相对路径（供 CSS background-image 使用）。

        :param filename: 原始文件名（仅取安全部分）
        :param data_url: 形如 data:image/png;base64,xxxx
        """
        safe = "".join(ch for ch in Path(filename or "bg.png").name if ch.isalnum() or ch in ".-_")
        if not safe.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")):
            safe += ".png"
        target = WEB_DIR / "assets" / "backgrounds" / f"{int(time.time())}_{safe}"
        self._save_data_url(target, data_url)
        rel = f"assets/backgrounds/{target.name}"
        self._settings.set("background_image", rel)
        return rel

    def remove_background(self) -> None:
        self._settings.set("background_image", None)

    def upload_avatar(self, kind: str, data_url: str) -> None:
        """保存头像（data URL 直接存设置，够小且离线可用）。kind: user|assistant"""
        if kind not in ("user", "assistant"):
            raise RuntimeError("未知的头像类型。")
        if not (data_url or "").startswith("data:"):
            raise RuntimeError("头像数据格式错误。")
        self._settings.set(f"{'user' if kind == 'user' else 'assistant'}.avatar", data_url)

    def _save_data_url(self, target: Path, data_url: str) -> None:
        try:
            header, _, b64 = data_url.partition(",")
            if "base64" not in header:
                raise ValueError("仅支持 base64 data URL")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(b64))
        except Exception as exc:  # noqa: BLE001
            log.exception("保存上传文件失败")
            raise RuntimeError(f"保存图片失败：{exc}") from exc

    # ------------------------------------------------------------------ #
    # 5) 窗口控制（托盘 / 全局热键 / 前端交互）
    # ------------------------------------------------------------------ #
    def show_window(self, window_id: str) -> bool:
        """显示并聚焦指定窗口（window_id: chat）。"""
        self._require_window().show(window_id)
        return True

    def hide_window(self, window_id: str) -> bool:
        """隐藏指定窗口。"""
        self._require_window().hide(window_id)
        return True

    def toggle_window(self, window_id: str) -> bool:
        """切换窗口显示/隐藏。"""
        self._require_window().toggle(window_id)
        return True

    def move_window(self, window_id: str, x: int, y: int) -> bool:
        """把窗口移动到 (x, y)。"""
        win = self._require_window()
        win.move(window_id, int(x), int(y))
        win.remember_position(window_id)
        return True

    def resize_window(self, window_id: str, width: int, height: int) -> bool:
        """调整窗口尺寸。"""
        # 节流日志：记录实际缩放（拖拽诊断用，最多每秒约 6 条）
        if not hasattr(self, "_resize_log_t"):
            self._resize_log_t = {}
        now_t = time.time()
        if now_t - self._resize_log_t.get(window_id, 0.0) >= 0.15:
            self._resize_log_t[window_id] = now_t
            log.info("resize_window %s -> %dx%d", window_id, width, height)
        self._require_window().resize(window_id, int(width), int(height))
        return True

    def refresh_window_transparency(self, window_id: str) -> bool:
        """
        兼容旧前端调用的空实现。

        窗口现在是不透明的，磨砂玻璃由页面内 CSS 实现，缩放不会再丢失合成，
        因此无需任何恢复动作。保留此方法只为避免旧版前端调用时报错。
        """
        log.debug("refresh_window_transparency 已废弃（窗口不透明）: %s", window_id)
        return True

    # ------------------------------------------------------------------ #
    # 6) 启动引导
    # ------------------------------------------------------------------ #
    def bootstrap(self) -> dict:
        """
        前端启动时一次性拉取所有上下文（减少往返）：
        settings / providers / themes / conversations / prompts / 当前主题 css。
        """
        return {
            "settings": self.get_settings(),
            "providers": self.get_providers(),
            "themes": self.get_themes(),
            "conversations": self.get_conversations(),
            "prompts": self.get_prompts(),
            "active_theme": self.get_active_theme(),
            "server_time": time.time(),
        }

    def get_active_theme(self) -> dict:
        """返回当前应用主题（含 css_url）。"""
        themes = {t["id"]: t for t in self.get_themes()}
        current = self._settings.get("theme") or "glassmorphism"
        theme = themes.get(current)
        if theme is None:
            theme = themes.get("glassmorphism") or (
                themes[next(iter(themes))] if themes else {}
            )
        return theme or {"id": current, "name": current, "css_url": "", "is_empty": True}

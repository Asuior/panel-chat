# -*- coding: utf-8 -*-
"""
窗口控制器：封装 pywebview 对话窗口的统一操作。

* 供托盘菜单、全局热键（任意线程）以及前端 API 调用；
* pywebview 6.x WinForms 后端内部通过 Invoke 封送到 GUI 线程，
  因此跨线程调用 show/hide/move/resize 是安全的；这里仍统一 try/except 兜底；
* 维护窗口位置记忆（moved/resized 事件触发时更新内存，隐藏/退出时落盘）。

窗口是**不透明**的。早期版本依赖 WebView2 的逐像素透明合成，缩放后会失效，
需要一整套多阶段自愈重刷机制才勉强维持；那套机制已整体移除。
现在的磨砂玻璃观感完全由页面内 CSS 实现（见 web/css/base.css 的
--backdrop-image 与 .app 的 backdrop-filter），与窗口合成无关。

本模块不 import webview，保持可无头导入。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

from core.logger import get_logger

log = get_logger(__name__)

WIN_CHAT = "chat"


class WindowController:
    def __init__(self, settings_manager):
        self._settings = settings_manager
        self._windows: dict[str, Any] = {}
        # 内存中的窗口几何信息（逻辑像素，落盘前不写文件）
        self._geometry: dict[str, dict] = {WIN_CHAT: {}}
        self._shown: dict[str, bool] = {WIN_CHAT: False}
        self._focus_hooked: set[str] = set()
        # 位置/尺寸落盘去抖定时器
        self._save_timers: dict[str, threading.Timer] = {}
        # 是否隐藏任务栏按钮。默认 False：窗口启动时先保留任务栏入口，
        # 等 main 确认托盘与热键都可用后再打开（见 apply_taskbar_policy）。
        self._hide_taskbar = False

    # ------------------------------------------------------------------ #
    # 注册
    # ------------------------------------------------------------------ #
    def register(self, window_id: str, window: Any, initially_visible: bool = True) -> None:
        """注册窗口并挂接位置记忆事件。"""
        self._windows[window_id] = window
        self._shown[window_id] = initially_visible
        self._geometry.setdefault(window_id, {})

        def _on_moved():
            self._remember(window_id)
            self._schedule_save(window_id)

        def _on_resized():
            self._remember(window_id)
            self._schedule_save(window_id)

        try:
            window.events.moved += _on_moved
            window.events.resized += _on_resized
        except Exception as exc:  # noqa: BLE001
            log.warning("挂接窗口事件失败(%s): %s", window_id, exc)

    def _remember(self, window_id: str) -> None:
        win = self._windows.get(window_id)
        if not win:
            return
        # 未显示过时不读取坐标：可能阻塞等待 shown 事件；以系统可见性为准
        if not self._shown.get(window_id, False) and self._os_visible(window_id) is not True:
            return
        try:
            self._geometry[window_id] = {"x": win.x, "y": win.y,
                                         "width": win.width, "height": win.height}
        except Exception:  # noqa: BLE001
            pass  # 窗口尚未 shown，忽略

    def _schedule_save(self, window_id: str, delay: float = 1.2) -> None:
        """位置/尺寸变化后去抖落盘：即使异常退出也能保留记忆。"""
        t = self._save_timers.pop(window_id, None)
        if t is not None:
            try:
                t.cancel()
            except Exception:  # noqa: BLE001
                pass
        t = threading.Timer(delay, self.remember_position, args=[window_id])
        t.daemon = True
        self._save_timers[window_id] = t
        t.start()

    def _window(self, window_id: str):
        win = self._windows.get(window_id)
        if win is None:
            raise RuntimeError(f"未知窗口：{window_id}")
        return win

    def _ready(self, win) -> bool:
        """GUI 尚未启动（webview.start 之前）时不执行窗口操作。"""
        return bool(getattr(win, "gui", None))

    # ------------------------------------------------------------------ #
    # 显示 / 隐藏
    # ------------------------------------------------------------------ #
    def show(self, window_id: str, focus: bool = True) -> None:
        win = self._window(window_id)
        if not self._ready(win):
            return
        try:
            # 解除 pywebview 的 focus=False 保护，避免激活时反复重加
            # WS_EX_NOACTIVATE 导致点击窗口无法获得键盘焦点
            if hasattr(win, "focus"):
                win.focus = True
            # 恢复“可被点击激活”，否则窗口保持 WS_EX_NOACTIVATE 时，
            # 从其它窗口点回来拿不到键盘焦点（光标闪烁却无法输入）
            if os.name == "nt":
                try:
                    self.enable_activation(window_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("恢复窗口激活(%s)失败: %s", window_id, exc)
            win.show()
            self._shown[window_id] = True
            self._remember(window_id)  # 显示成功后立即记录位置（若已触发事件则刷新）
            log.info("窗口已显示: %s", window_id)
            # 每次显示都顺带重申任务栏策略（WinForms 的 Show 可能重置窗口样式）。
            # 注意：仅在 allow 时才真的隐藏，见 apply_taskbar_policy。
            if os.name == "nt":
                self._reassert_taskbar_policy(window_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("show(%s) 失败: %s", window_id, exc)

    def hide(self, window_id: str) -> None:
        win = self._window(window_id)
        if not self._ready(win):
            return
        try:
            self.remember_position(window_id)  # 先记位置再隐藏
            win.hide()
            self._shown[window_id] = False
            log.info("窗口已隐藏: %s", window_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("hide(%s) 失败: %s", window_id, exc)

    def toggle(self, window_id: str) -> bool:
        """切换显示状态，返回切换后是否可见。

        先以系统真实可见性校正内部记录（pywebview 的 hidden 创建
        可能令窗口启动即可见而记录为 False，导致热键第一次无效）。
        """
        self._window(window_id)
        vis = self._os_visible(window_id)
        if vis is not None:
            self._shown[window_id] = vis
        is_visible = self.is_visible(window_id)
        if is_visible:
            self.hide(window_id)
            return False
        self.show(window_id)
        return True

    def _os_visible(self, window_id: str):
        """查询系统真实可见性；无法判断时返回 None（非 Windows / 句柄未就绪）。"""
        if os.name != "nt":
            return None
        hwnd = self.window_handle(window_id)
        if not hwnd:
            return None
        try:
            import ctypes

            return bool(ctypes.windll.user32.IsWindowVisible(ctypes.c_void_p(hwnd)))
        except Exception:  # noqa: BLE001
            return None

    def sync_visibility(self, window_id: str) -> None:
        """启动后校正：以系统真实可见性为准（窗口可能启动即可见）。"""
        vis = self._os_visible(window_id)
        if vis is not None:
            self._shown[window_id] = vis
            log.info("窗口可见性校正 %s -> %s", window_id, vis)

    def is_visible(self, window_id: str) -> bool:
        """基于内部状态判断可见性（pywebview 未暴露动态 visible 属性）。"""
        return bool(self._shown.get(window_id, False))

    def move(self, window_id: str, x: int, y: int) -> None:
        self._window(window_id).move(int(x), int(y))

    def resize(self, window_id: str, width: int, height: int) -> None:
        self._window(window_id).resize(int(width), int(height))

    # ------------------------------------------------------------------ #
    # 任务栏策略
    # ------------------------------------------------------------------ #
    def apply_taskbar_policy(self, hide: bool) -> bool:
        """
        根据“逃生通道是否可用”决定是否隐藏任务栏按钮。

        本窗口是工具窗口：隐藏任务栏按钮后，任务栏与 Alt+Tab 都没有入口，
        窗口一旦被隐藏，只能靠托盘或全局热键找回。而托盘是**允许失败**的
        （缺少 pystray/Pillow、图标创建异常等），热键也可能被占用或权限不足。
        因此只有在**托盘与热键都确实可用**时才隐藏任务栏按钮；
        否则保留它作为兜底入口，避免应用彻底隐形。

        :param hide: True = 隐藏任务栏按钮（正常路径）；False = 保留（降级路径）
        :return: 最终是否成功隐藏
        """
        self._hide_taskbar = bool(hide)
        if not hide:
            log.warning(
                "托盘或全局热键不可用 → 保留任务栏按钮，作为找回窗口的兜底入口"
            )
            return False
        return self.hide_taskbar_button(WIN_CHAT)

    def _reassert_taskbar_policy(self, window_id: str) -> None:
        """重申任务栏策略（幂等）。策略为“保留”时什么都不做。"""
        if not self._hide_taskbar:
            return
        try:
            self.hide_taskbar_button(window_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("隐藏任务栏按钮失败(%s): %s", window_id, exc)

    # ------------------------------------------------------------------ #
    # 原生窗口
    # ------------------------------------------------------------------ #
    def window_handle(self, window_id: str):
        """返回窗口的原生句柄（int），未就绪时返回 None。"""
        win = self._windows.get(window_id)
        if win is None:
            return None
        native = getattr(win, "native", None)
        if native is None:
            return None
        handle = getattr(native, "Handle", None)
        if handle is None:
            return None
        try:
            return int(handle)
        except (TypeError, ValueError):
            pass
        for method in ("ToInt64", "ToInt32"):
            fn = getattr(handle, method, None)
            if fn is not None:
                try:
                    return int(fn())
                except Exception:  # noqa: BLE001
                    continue
        return None

    def apply_round_corners(self, window_id: str, style: str = "round") -> bool:
        """
        给无边框窗口加系统圆角（Windows 11，仅 Windows）。

        **实测确认在 frameless 窗口上生效**：用差分截图比对施加 DWM 圆角前后的
        左下角区域，出现 131 个强差异像素（最大通道差 62）——即窗口角落真的被
        裁掉、露出了后面的桌面。（对比：`SetWindowRgn` 半径 16 时为 275 个强差异
        像素、最大 142，说明 DWM 的半径确实更小，与 `ROUND`≈8px 一致。）

        局限：**半径不可指定**，只能选系统给定的档位：
            round      ≈ 8px（Win11 默认，抗锯齿，推荐）
            roundsmall ≈ 4px
            none       关闭
        需要更大的半径只能改用 `SetWindowRgn` + `CreateRoundRectRgn`
        （半径可控，但边缘无抗锯齿）。详见 REFACTOR_PLAN.md 坑 A。

        注意：窗口被 DWM 裁圆后，CSS 侧的 `--window-radius` 应保持 0，
        否则会出现双圆角。幂等。
        """
        if os.name != "nt":
            return False
        hwnd = self.window_handle(window_id)
        if not hwnd:
            return False
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        # 0=DEFAULT 1=DONOTROUND 2=ROUND 3=ROUNDSMALL
        pref = {"default": 0, "none": 1, "round": 2, "roundsmall": 3}.get(style, 2)
        try:
            import ctypes
            from ctypes import wintypes

            dwmapi = ctypes.windll.dwmapi
            dwmapi.DwmSetWindowAttribute.argtypes = [
                wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
            val = ctypes.c_int(pref)
            hr = dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(val), ctypes.sizeof(val),
            )
            if hr != 0:
                # Win10 / 不支持时返回错误，忽略即可
                log.debug("设置窗口圆角失败(%s) hr=0x%08X", window_id, hr & 0xFFFFFFFF)
                return False
            log.info("已设置窗口圆角 %s -> %s", window_id, style)
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug("apply_round_corners(%s) 失败: %s", window_id, exc)
            return False

    def remove_layered_window(self, window_id: str) -> bool:
        """
        清除 WS_EX_LAYERED（仅 Windows）。

        窗口是**不透明**的，本不该是分层窗口。之所以会带上这个样式：
        pywebview 在 `create_window(hidden=True)` 时会做一次
        `Opacity = 0 → Show → Hide → Opacity = 1`
        （见 python/Lib/site-packages/webview/platforms/winforms.py 的 create_window），
        而 WinForms 一旦设置过 `Form.Opacity` 就会给窗口加上 WS_EX_LAYERED，
        且在 Opacity 复原后**不会**移除它。

        留着它的坏处是窗口仍然走分层合成路径 —— 正是本次重构要摆脱的那套
        不确定机制（分层 + 逐像素 alpha 一旦与 WebView2 的 DirectComposition
        表面不同步，就会出现「缩放后变白底」这类问题）。窗口内容本身已由页面
        绘制为不透明，因此清掉这个样式视觉上没有任何变化。

        幂等：样式本来就没置上时直接返回，不做任何 SetWindowPos 以免多余的重绘。
        """
        if os.name != "nt":
            return False
        hwnd = self.window_handle(window_id)
        if not hwnd:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020

            get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
            set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            get_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            set_long.restype = ctypes.c_ssize_t
            user32.SetWindowPos.argtypes = [
                wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint,
            ]

            style = get_long(hwnd, GWL_EXSTYLE)
            if not (style & WS_EX_LAYERED):
                return True  # 本来就不是分层窗口
            set_long(hwnd, GWL_EXSTYLE, style & ~WS_EX_LAYERED)
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER |
                                SWP_NOACTIVATE | SWP_FRAMECHANGED)
            log.info("已清除 WS_EX_LAYERED（窗口不再走分层合成）: %s", window_id)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("remove_layered_window(%s) 失败: %s", window_id, exc)
            return False

    def hide_taskbar_button(self, window_id: str) -> bool:
        """
        隐藏窗口的任务栏按钮（仅 Windows）。

        原理：给窗口扩展样式追加 WS_EX_TOOLWINDOW 并清除 WS_EX_APPWINDOW。
        工具窗口不会出现在任务栏与 Alt+Tab 中，是托盘常驻工具的常见做法。
        幂等，可重复调用。

        调用前请先经 apply_taskbar_policy 确认逃生通道可用。
        """
        if os.name != "nt":
            return False
        hwnd = self.window_handle(window_id)
        if not hwnd:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020

            get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
            set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            get_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            set_long.restype = ctypes.c_ssize_t
            user32.SetWindowPos.argtypes = [
                wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint,
            ]

            style = get_long(hwnd, GWL_EXSTYLE)
            style |= WS_EX_TOOLWINDOW
            style &= ~WS_EX_APPWINDOW
            set_long(hwnd, GWL_EXSTYLE, style)
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER |
                                SWP_NOACTIVATE | SWP_FRAMECHANGED)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("hide_taskbar_button(%s) 失败: %s", window_id, exc)
            return False

    def enable_activation(self, window_id: str) -> bool:
        """
        清除 WS_EX_NOACTIVATE，让窗口可以重新被点击激活并接收键盘输入。

        pywebview 在 create_window(focus=False) 时会附加 WS_EX_NOACTIVATE，
        其副作用是：从其它窗口点回来时窗口不会激活，WebView 输入框
        只有 DOM 焦点（光标闪烁）却拿不到键盘。本方法幂等。

        注意：pywebview 的 winforms 后端在每次 Activated 事件里，
        若 window.focus 仍为 False 会重新加回该样式；因此调用前请先把
        对应 pywebview Window 对象的 .focus 置为 True。
        """
        if os.name != "nt":
            return False
        hwnd = self.window_handle(window_id)
        if not hwnd:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020

            get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
            set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            get_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            set_long.restype = ctypes.c_ssize_t

            style = get_long(hwnd, GWL_EXSTYLE)
            if not (style & WS_EX_NOACTIVATE):
                return True  # 本来就可激活
            set_long(hwnd, GWL_EXSTYLE, style & ~WS_EX_NOACTIVATE)
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER |
                                SWP_NOACTIVATE | SWP_FRAMECHANGED)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("enable_activation(%s) 失败: %s", window_id, exc)
            return False

    def focus_webview_on_activate(self, window_id: str) -> None:
        """
        挂接 WinForms Activated 事件：窗口被（重新）激活时把键盘焦点
        直接交给 WebView2 控件（等价于 pywebview 隐藏后再显示的聚焦路径）。
        仅挂接一次。
        """
        if os.name != "nt":
            return
        win = self._windows.get(window_id)
        if win is None:
            return
        native = getattr(win, "native", None)
        if native is None or window_id in self._focus_hooked:
            return

        def _on_activated(sender, _args):
            try:
                wv = getattr(native, "webview", None)
                if wv is not None and hasattr(wv, "Focus"):
                    wv.Focus()
            except Exception as exc:  # noqa: BLE001
                log.debug("聚焦 WebView 失败(%s): %s", window_id, exc)

        try:
            native.Activated += _on_activated
            self._focus_hooked.add(window_id)
            log.info("已挂接窗口激活聚焦: %s", window_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("挂接 Activated 事件失败(%s): %s", window_id, exc)

    def destroy_all(self) -> None:
        """退出程序时销毁全部窗口。"""
        for wid, win in list(self._windows.items()):
            try:
                self.remember_position(wid)
                win.destroy()
            except Exception as exc:  # noqa: BLE001
                log.warning("销毁窗口 %s 失败: %s", wid, exc)

    # ------------------------------------------------------------------ #
    # 几何记忆
    # ------------------------------------------------------------------ #
    def remember_position(self, window_id: str) -> None:
        """把内存中的位置（含宽高）记忆落盘。"""
        geo = self._geometry.get(window_id)
        if not geo or geo.get("x") is None:
            # 尚无内存位置：若窗口可见则立即读取一次
            if self._shown.get(window_id, False) or self._os_visible(window_id) is True:
                self._remember(window_id)
                geo = self._geometry.get(window_id)
        if geo and geo.get("x") is not None:
            self._settings.remember_window_geometry(
                window_id, geo["x"], geo["y"],
                geo.get("width"), geo.get("height"),
            )

    def geometry(self, window_id: str) -> dict:
        return dict(self._geometry.get(window_id, {}))

    # ------------------------------------------------------------------ #
    # 事件推送
    # ------------------------------------------------------------------ #
    def notify(self, window_id: str, event_name: str, data: Any = None) -> None:
        """
        主动向某窗口推送事件：
            window.dispatchEvent(new CustomEvent(event_name, {detail: data}))
        """
        win = self._windows.get(window_id)
        if win is None or not self._ready(win):
            return
        payload = json.dumps(data, ensure_ascii=False)
        script = (
            "window.dispatchEvent(new CustomEvent("
            + json.dumps(event_name, ensure_ascii=False)
            + ", {detail: "
            + payload
            + "}));"
        )
        try:
            win.evaluate_js(script)
        except Exception as exc:  # noqa: BLE001
            log.warning("推送事件 %s -> %s 失败: %s", event_name, window_id, exc)

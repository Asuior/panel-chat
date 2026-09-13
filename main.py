# -*- coding: utf-8 -*-
"""
Alice —— 桌面快捷 AI 辅助工具（程序入口）。

启动流程：
    1. 检查运行依赖（pywebview / keyboard / pystray / Pillow）；
    2. 初始化数据目录与各类管理器（设置/提示词/会话/插件）；
    3. 创建一个 PyWebView 窗口（对话窗口：无边框 / 不透明 / 不进任务栏）；
    4. 启动系统托盘（pystray）与全局热键（keyboard）；
    5. 进入 PyWebView 事件循环。

窗口可见性：
    默认**静默启动**（窗口隐藏，靠托盘或热键 Ctrl+Alt+W 唤出），方便开机自启；
    首次运行（data/settings.json 不存在）时显示一次，避免新用户以为程序没启动。
    该行为由 settings.json 的 start_hidden 控制。

任务栏：
    对话窗口是工具窗口（WS_EX_TOOLWINDOW），不进任务栏也不进 Alt+Tab；
    但只有**托盘与全局热键都可用**时才真的隐藏任务栏按钮，否则保留它作为
    兜底入口（详见 WindowController.apply_taskbar_policy）。

用法：  python main.py
调试：  ALICE_DEBUG=1 python main.py   （打开 WebView2 开发者工具）
"""

from __future__ import annotations


import os
import sys
import threading

# 允许从任意目录启动（python D:\...\main.py）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.logger import get_logger, setup_logging  # noqa: E402

setup_logging()
log = get_logger("main")

# 延迟导入：先做依赖检查，给出友好提示 -------------------------------- #
def force_dpi_awareness() -> None:
    """把进程的 DPI 感知模式**在创建任何线程/窗口之前**定下来。

    不这么做的话，托盘右键菜单的大小会飘（有时正常、有时边距明显偏大）：

    * `python.exe` 的 manifest 里没有 `dpiAware`，进程默认是**不感知 DPI**；
    * 真正把它改成 SystemAware 的是 .NET/WinForms —— 也就是 pywebview
      建窗那一刻，发生在 GUI 线程上；
    * 而 pystray 的托盘线程（`icon.run`）跟它是并发的，它那两个窗口
      （`_hwnd` 收托盘消息、`_menu_hwnd` 当菜单宿主）有可能在改之前建出来，
      也可能在改之后；
    * `TrackPopupMenuEx` 是**按菜单宿主窗口的感知模式**画的：宿主是 unaware
      时系统会把整张菜单按 96 DPI 画完再位图拉伸到 150%，于是又大又糊；
      宿主 aware 时才是原生尺寸。

    实测确认过：同一次运行里两个托盘窗口一个是 `SYSTEM_AWARE`(dpi=144)、
    另一个是 `UNAWARE`(dpi=96)。这里统一设成 per-monitor v2
    （也是 WebView2 期望的模式），所有窗口从一开始就用同一个上下文，竞态消失。
    """
    if sys.platform != "win32":
        return
    import ctypes

    if not hasattr(ctypes, "windll"):
        return
    # Win10 1703+：DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == -4
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # Win8.1+
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()        # Vista+
    except Exception:  # noqa: BLE001
        pass


def check_dependencies() -> list[str]:
    """检查核心运行依赖，返回缺失列表。"""
    missing: list[str] = []
    try:
        import webview  # noqa: F401
    except ImportError:
        missing.append("pywebview")
    try:
        import keyboard  # noqa: F401
    except ImportError:
        missing.append("keyboard")
    try:
        import pystray  # noqa: F401
    except ImportError:
        missing.append("pystray")
    try:
        import PIL  # noqa: F401
    except ImportError:
        missing.append("Pillow")
    return missing


def _create_backend():
    """创建全部业务对象（可被无头测试复用）。"""
    from core.api import Api  # noqa: F401
    from core.conversation import ConversationManager
    from core.plugin_manager import PluginManager
    from core.prompt_manager import PromptManager
    from core.settings_manager import SettingsManager
    from core.window_controller import WIN_CHAT, WindowController

    settings = SettingsManager()
    prompts = PromptManager()
    conversations = ConversationManager()
    plugins = PluginManager()
    plugins.load_plugins()

    controller = WindowController(settings)

    api = Api(
        settings=settings,
        prompts=prompts,
        conversations=conversations,
        plugins=plugins,
        window_ops=controller,
    )

    return {
        "settings": settings,
        "prompts": prompts,
        "conversations": conversations,
        "plugins": plugins,
        "controller": controller,
        "api": api,
        "WIN_CHAT": WIN_CHAT,
    }


def _primary_area():
    """返回主屏（逻辑坐标）工作区，用于默认摆位。"""
    try:
        import webview

        screens = webview.screens
        if screens:
            s = screens[0]
            return s.x, s.y, s.width, s.height
    except Exception:  # noqa: BLE001
        pass
    return 0, 0, 1920, 1080


def _all_areas():
    """返回全部屏幕（逻辑坐标）工作区列表；失败时退回主屏。"""
    try:
        import webview

        screens = webview.screens
        if screens:
            return [(s.x, s.y, s.width, s.height) for s in screens]
    except Exception:  # noqa: BLE001
        pass
    return [_primary_area()]


def _clamp_position(settings, window_id: str, width: int, height: int):
    """
    读取记忆位置并保证窗口仍可见。

    · 只要窗口在**任意一块屏幕**内还留有足够可见区域，就按记忆位置恢复
      （多屏用户把窗口放在副屏时不会被强行拉回主屏）；
    · 若完全落在所有屏幕之外（例如副屏被拔掉），再夹回第一块屏幕。
    """
    x, y = settings.window_position(window_id)
    if x is None or y is None:
        return None, None
    areas = _all_areas()
    for (sx, sy, sw, sh) in areas:
        if (x + width - 80 > sx and x < sx + sw - 80
                and y + height - 40 > sy and y < sy + sh - 40):
            return int(x), int(y)
    sx, sy, sw, sh = areas[0]
    x = max(sx - width + 80, min(x, sx + sw - 80))
    y = max(sy, min(y, sy + sh - 60))
    return int(x), int(y)


class AliceApp:
    """应用主体：窗口、托盘、热键、事件循环。"""

    def __init__(self, backend: dict):
        self.b = backend
        self.settings = backend["settings"]
        self.controller = backend["controller"]
        self.api = backend["api"]
        self.windows: dict[str, object] = {}
        self._hotkey_handles: list = []
        self._tray = None
        self._tray_thread: threading.Thread | None = None
        self._quitting = threading.Event()
        # 首次运行（无 settings.json）：强制显示一次窗口
        self._first_run = bool(getattr(self.settings, "first_run", False))
        # 目标启动可见性：静默启动 = 启动后隐藏
        self._start_hidden = bool(self.settings.get("start_hidden", True))
        if self._first_run:
            self._start_hidden = False

    # ------------------------------------------------------------------ #
    # 窗口创建
    # ------------------------------------------------------------------ #
    def build_windows(self):
        import webview

        from core.paths import web_url

        WIN_CHAT = self.b["WIN_CHAT"]
        chat_min = (int(self.settings.get("chat.min_width", 500)),
                    int(self.settings.get("chat.min_height", 600)))
        # 恢复上次的窗口尺寸（用户可调），并夹在 [最小值, 主屏工作区] 之间
        chat_w = int(self.settings.get("chat.width", 500) or 500)
        chat_h = int(self.settings.get("chat.height", 700) or 700)
        sx0, sy0, sw0, sh0 = _primary_area()
        chat_w = max(chat_min[0], min(chat_w, max(chat_min[0], sw0)))
        chat_h = max(chat_min[1], min(chat_h, max(chat_min[1], sh0)))

        cx, cy = _clamp_position(self.settings, WIN_CHAT, chat_w, chat_h)
        if cx is None:
            sx, sy, sw, sh = _primary_area()
            cx, cy = sx + sw - chat_w - 90, sy + (sh - chat_h) // 2 - 40

        hidden = self._start_hidden
        if self._first_run:
            log.info("首次运行（尚无 settings.json）：启动时显示窗口一次")
        else:
            log.info("启动可见性：%s", "隐藏（静默启动）" if hidden else "显示")

        chat = webview.create_window(
            "Alice",
            url=web_url("index.html"),
            js_api=self.api,
            width=chat_w,
            height=chat_h,
            x=cx,
            y=cy,
            resizable=True,
            min_size=chat_min,
            frameless=True,
            # 不透明：磨砂玻璃观感完全由页面内 CSS 实现，不依赖窗口级透明合成
            transparent=False,
            # 与 web/css/base.css 的 --window-base 保持一致，
            # 避免窗口显示瞬间在页面绘制前闪一下 WebView2 的默认白底
            background_color="#ECEEF6",
            # 始终置顶（本窗口是工具窗口、没有任务栏入口，取消置顶后就再也
            # 找不回它了；置顶与“不进任务栏”是一组绑定取舍）
            on_top=True,
            easy_drag=False,         # 通过 .pywebview-drag-region 头部拖拽
            hidden=hidden,
            shadow=False,
            text_select=True,
            focus=False,
        )

        self.windows = {WIN_CHAT: chat}
        self.controller.register(WIN_CHAT, chat, initially_visible=not hidden)
        return self.windows

    # ------------------------------------------------------------------ #
    # 托盘
    # ------------------------------------------------------------------ #
    def start_tray(self):
        """启动系统托盘；失败仅告警并降级，不阻断主程序。"""
        try:
            self._start_tray_impl()
        except Exception as exc:  # noqa: BLE001
            log.exception("系统托盘启动失败")
            print(f"[警告] 系统托盘启动失败（{exc}）。")
            self._warn_degraded(f"系统托盘启动失败（{exc}）")

    def _warn_degraded(self, reason: str) -> None:
        """逃生通道不可用时的提示：保留任务栏按钮，并尽量让用户看见原因。"""
        message = (
            f"{reason}。为保证仍能找回窗口，已保留任务栏按钮"
            "（正常情况下它会被隐藏）。"
        )
        log.warning(message)
        print(f"[警告] {message}")
        # 托盘若可用，用气泡再提示一次；不可用时只能靠日志与任务栏
        if self._tray is not None:
            try:
                self._tray.notify(message, "Alice")
            except Exception:  # noqa: BLE001
                pass

    def _start_tray_impl(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError as exc:
            log.warning("无法启动系统托盘（缺少依赖 %s）", exc)
            self._warn_degraded(f"无法启动系统托盘（缺少依赖 {exc}）")
            return

        WIN_CHAT = self.b["WIN_CHAT"]
        controller, settings = self.controller, self.settings

        # --- 主题子菜单 ---
        def _themes_menu():
            import pystray as _pt

            themes = self.api.get_themes() or []

            def _apply(theme_id):
                try:
                    theme = self.api.apply_theme(theme_id)
                    controller.notify(WIN_CHAT, "theme-changed", theme)
                except Exception as exc:  # noqa: BLE001
                    log.warning("切换主题失败: %s", exc)

            # pystray 的动作回调只接受 (icon, item) 两个参数，
            # 用闭包工厂把要切换的主题 id 固化进去。
            def _make_theme_action(theme_id):
                def _action(icon, item):
                    _apply(theme_id)

                return _action

            return _pt.Menu(
                *[
                    _pt.MenuItem(
                        f"● {t['name']}" if t["id"] == settings.get("theme") else t["name"],
                        _make_theme_action(t["id"]),
                    )
                    for t in themes
                ]
            )

        # --- AI 接口子菜单 ---
        def _providers_menu():
            import pystray as _pt

            providers = self.api.get_providers() or []

            def _set(pid):
                settings.set("provider", pid)
                controller.notify(WIN_CHAT, "provider-changed", {"id": pid})
                log.info("托盘切换 AI 接口 -> %s", pid)

            def _make_provider_action(pid):
                def _action(icon, item):
                    _set(pid)

                return _action

            return _pt.Menu(
                *[
                    _pt.MenuItem(
                        f"● {p['name']}" if p["id"] == settings.get("provider") else p["name"],
                        _make_provider_action(p["id"]),
                    )
                    for p in providers
                ]
            )

        menu = pystray.Menu(
            pystray.MenuItem(
                "显示/隐藏 对话窗口",
                lambda _i, _it: controller.toggle(WIN_CHAT),
                default=True,        # 左键单击托盘图标即切换窗口
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("切换主题", _themes_menu()),
            pystray.MenuItem("切换 AI 接口", _providers_menu()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出程序", lambda _i, _it: self.quit()),
        )

        # image = self._make_tray_icon()
        image = Image.open("web/assets/icons/icon.png")
        icon = pystray.Icon("alice-ai-assistant", image, "Alice AI 助手", menu)
        self._tray = icon
        self._tray_thread = threading.Thread(target=icon.run, daemon=True, name="tray")
        self._tray_thread.start()
        log.info("系统托盘已启动")

    @staticmethod
    def _make_tray_icon():
        from PIL import Image, ImageDraw

        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # 渐变感近似：双层圆
        draw.ellipse((2, 2, size - 2, size - 2), fill=(124, 77, 255, 255))
        draw.ellipse((8, 8, size - 8, size - 8), fill=(0, 188, 212, 255))
        draw.ellipse((22, 22, size - 22, size - 22), fill=(255, 255, 255, 230))
        return img

    # ------------------------------------------------------------------ #
    # 全局热键
    # ------------------------------------------------------------------ #
    def start_hotkeys(self):
        """注册全局热键。注册失败会记录在案，供任务栏降级判断使用。"""
        try:
            import keyboard
        except ImportError:
            log.warning("keyboard 不可用，全局热键未注册。")
            self._warn_degraded("keyboard 库不可用，全局热键未注册")
            return

        WIN_CHAT = self.b["WIN_CHAT"]
        hotkeys = self.settings.get("hotkeys") or {}
        key = hotkeys.get("toggle_chat")
        if not key:
            log.warning("未配置 toggle_chat 热键。")
            self._warn_degraded("未配置全局热键 toggle_chat")
            return

        def _cb():
            try:
                self.controller.toggle(WIN_CHAT)
            except Exception as exc:  # noqa: BLE001
                log.warning("热键切换窗口失败: %s", exc)

        try:
            handle = keyboard.add_hotkey(key, _cb)
            self._hotkey_handles.append((handle, key))
            log.info("全局热键已注册: %s -> %s", key, WIN_CHAT)
        except Exception as exc:  # noqa: BLE001
            log.warning("热键 %s 注册失败（可能被占用或权限不足）: %s", key, exc)
            self._warn_degraded(f"热键 {key} 注册失败（可能被占用或权限不足）")

    # ------------------------------------------------------------------ #
    def quit(self):
        """退出程序：托盘 → 热键 → 位置落盘 → 销毁窗口。"""
        if self._quitting.is_set():
            return
        self._quitting.set()
        log.info("正在退出…")
        try:
            for handle, _key in self._hotkey_handles:
                try:
                    import keyboard

                    keyboard.remove_hotkey(handle)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:  # noqa: BLE001
                pass
        self.controller.remember_position(self.b["WIN_CHAT"])
        self.controller.destroy_all()
        # 兜底：若 GUI 循环未能自然退出，4 秒后强制结束进程
        threading.Timer(4.0, os._exit, args=[0]).start()

    # ------------------------------------------------------------------ #
    def _escape_hatch_available(self) -> bool:
        """
        逃生通道是否可用 = 托盘起来了 **且** 至少注册到一个全局热键。

        本窗口不进任务栏、也不进 Alt+Tab；一旦隐藏，只能靠托盘或热键找回。
        两者缺一就不能安全地隐藏任务栏按钮（见 WindowController.apply_taskbar_policy）。
        抽成独立方法是为了让冒烟测试能覆盖这段判定，而不必真的跑 GUI。
        """
        return (self._tray is not None) and bool(self._hotkey_handles)

    # ------------------------------------------------------------------ #
    def _on_gui_started(self):
        """GUI 循环启动后（子线程）：
        1) 等待原生窗口句柄就绪；
        2) 按“托盘 + 热键是否都可用”决定是否隐藏任务栏按钮（逃生通道）；
        3) 落实启动可见性（静默启动时显式隐藏一次）；
        4) 让窗口从此可被点击激活（启动早期 focus=False 会令 pywebview
           在每次 Activated 事件里反复重加 WS_EX_NOACTIVATE —— 把 focus 标记
           翻为 True 即可让它不再重加，配合 enable_activation 清除现有样式）；
        5) 窗口激活时强制给 WebView2 控件焦点（键盘直达）。
        """
        import time

        WIN_CHAT = self.b["WIN_CHAT"]
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.controller.window_handle(WIN_CHAT) is not None:
                break
            time.sleep(0.2)

        # --- 不透明：清掉 pywebview 因 hidden=True 的 Opacity 操作而留下的
        #         WS_EX_LAYERED，避免窗口继续走分层合成路径（见方法注释） ---
        try:
            self.controller.remove_layered_window(WIN_CHAT)
        except Exception as exc:  # noqa: BLE001
            log.warning("清除分层窗口样式失败: %s", exc)

        # --- 圆角：DWM 系统圆角（Win11）。已实测在 frameless 窗口上生效 ---
        if self.settings.get("round_corners", True):
            try:
                self.controller.apply_round_corners(WIN_CHAT, "round")
            except Exception as exc:  # noqa: BLE001
                log.warning("设置窗口圆角失败: %s", exc)

        # --- 任务栏：只有逃生通道（托盘 + 热键）都可用时才隐藏按钮 ---
        safe = self._escape_hatch_available()
        try:
            hidden = self.controller.apply_taskbar_policy(safe)
            log.info("任务栏按钮隐藏=%s（托盘=%s，热键=%d 个）",
                     hidden, self._tray is not None, len(self._hotkey_handles))
        except Exception as exc:  # noqa: BLE001
            log.warning("应用任务栏策略失败: %s", exc)

        # --- 焦点与激活 ---
        try:
            win = self.windows.get(WIN_CHAT)
            if win is not None and hasattr(win, "focus"):
                win.focus = True
            self.controller.enable_activation(WIN_CHAT)
        except Exception as exc:  # noqa: BLE001
            log.warning("恢复窗口激活失败: %s", exc)

        # --- 可见性：以系统真实可见性校正，并落实静默启动 ---
        try:
            self.controller.sync_visibility(WIN_CHAT)
            if self._start_hidden and self.controller.is_visible(WIN_CHAT):
                # pywebview 的 hidden=True 在某些合成路径下未必真的隐藏，
                # 这里再显式隐藏一次，确保静默启动一定成立。
                log.info("落实静默启动：显式隐藏窗口")
                self.controller.hide(WIN_CHAT)
        except Exception as exc:  # noqa: BLE001
            log.warning("落实启动可见性失败: %s", exc)

        try:
            self.controller.focus_webview_on_activate(WIN_CHAT)
        except Exception as exc:  # noqa: BLE001
            log.warning("挂接 WebView 聚焦失败: %s", exc)

    # ------------------------------------------------------------------ #
    def run(self):
        import webview

        self.build_windows()
        self.start_hotkeys()
        self.start_tray()

        debug = os.environ.get("ALICE_DEBUG") == "1"
        log.info("进入 GUI 事件循环（debug=%s）", debug)
        try:
            webview.start(func=self._on_gui_started, gui="edgechromium", debug=debug,)
        except Exception as exc:  # noqa: BLE001
            log.exception("GUI 启动失败")
            print(f"\n[错误] GUI 启动失败：{exc}")
            print("请确认已安装 WebView2 Runtime（Win10/11 + Edge 一般自带）。")
            self.quit()
            return 1
        log.info("程序已退出。")
        return 0


def main() -> int:
    # 必须最先执行：托盘线程会在建窗之前起来，感知模式定晚了菜单就会飘
    force_dpi_awareness()

    missing = check_dependencies()
    if missing:
        names = "、".join(missing)
        print(
            "=" * 60
            + f"\n缺少运行依赖：{names}\n\n"
            "请先安装（不会自动安装，避免污染环境）：\n\n"
            f"    pip install {' '.join(missing)}\n"
            "完整依赖见 requirements.txt\n"
            "=" * 60
        )
        return 1

    from core.paths import ensure_dirs

    ensure_dirs()
    backend = _create_backend()
    app = AliceApp(backend)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""
Alice —— 桌面快捷 AI 辅助工具（程序入口）。

启动流程：
    1. 检查运行依赖（pywebview / keyboard / pystray / Pillow）；
    2. 初始化数据目录与各类管理器（设置/提示词/会话/插件）；
    3. 创建两个 PyWebView 窗口（悬浮球 + 悬浮窗）；
    4. 启动系统托盘（pystray）与全局热键（keyboard）；
    5. 进入 PyWebView 事件循环。

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
    from core.window_controller import WIN_BALL, WIN_CHAT, WindowController

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
        "WIN_BALL": WIN_BALL,
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
      （多屏用户把悬浮球/聊天窗放在副屏时不会被强行拉回主屏）；
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

    # ------------------------------------------------------------------ #
    # 窗口创建
    # ------------------------------------------------------------------ #
    def build_windows(self):
        import webview

        from core.paths import web_url

        WIN_BALL, WIN_CHAT = self.b["WIN_BALL"], self.b["WIN_CHAT"]
        ball_diameter = int(self.settings.get("ball.size", 70) or 70)
        # 窗口比球大一圈（四周各留 16px 透明边距）：
        # 保证球是正圆，且外发光（≤16px）在窗口内自然衰减、不会被边界截成矩形
        ball_win = ball_diameter + 32
        chat_min = (int(self.settings.get("chat.min_width", 500)),
                    int(self.settings.get("chat.min_height", 600)))
        # 恢复上次的窗口尺寸（用户可调），并夹在 [最小值, 主屏工作区] 之间
        chat_w = int(self.settings.get("chat.width", 420) or 420)
        chat_h = int(self.settings.get("chat.height", 700) or 700)
        sx0, sy0, sw0, sh0 = _primary_area()
        chat_w = max(chat_min[0], min(chat_w, max(chat_min[0], sw0)))
        chat_h = max(chat_min[1], min(chat_h, max(chat_min[1], sh0)))

        # --- 悬浮球 ---
        bx, by = _clamp_position(self.settings, WIN_BALL, ball_win, ball_win)
        if bx is None:
            sx, sy, sw, sh = _primary_area()
            bx, by = sx + sw - ball_win - 48, sy + sh - ball_win - 160
        ball = webview.create_window(
            "悬浮球",
            url=web_url("floatball.html"),
            js_api=self.api,
            width=ball_win,
            height=ball_win,
            x=bx,
            y=by,
            resizable=False,
            frameless=True,
            transparent=True,
            on_top=True,
            easy_drag=True,          # 整球可拖
            hidden=False,
            shadow=False,
            text_select=False,
            focus=False,
        )

        # --- 悬浮窗（聊天） ---
        cx, cy = _clamp_position(self.settings, WIN_CHAT, chat_w, chat_h)
        if cx is None:
            sx, sy, sw, sh = _primary_area()
            cx, cy = sx + sw - chat_w - 90, sy + (sh - chat_h) // 2 - 40
        chat = webview.create_window(
            "悬浮窗",
            url=web_url("index.html"),
            js_api=self.api,
            width=chat_w,
            height=chat_h,
            x=cx,
            y=cy,
            resizable=True,
            min_size=chat_min,
            frameless=True,
            transparent=True,
            # 悬浮窗始终置顶（已移除“取消置顶”功能：本窗口是工具窗口、
            # 没有任务栏入口，一旦取消置顶并显示桌面后就无法再找到它）
            on_top=True,
            easy_drag=False,         # 通过 .pywebview-drag-region 头部拖拽
            hidden=True,
            shadow=False,
            text_select=True,
            focus=False,
        )

        self.windows = {WIN_BALL: ball, WIN_CHAT: chat}
        self.controller.register(WIN_BALL, ball, initially_visible=True)
        self.controller.register(WIN_CHAT, chat, initially_visible=False)
        return self.windows

    # ------------------------------------------------------------------ #
    # 文件拖拽处理
    # ------------------------------------------------------------------ #
    def _on_files_dropped(self, event: dict) -> None:
        """悬浮球收到系统文件拖放：逐个处理并把结果推送到两个窗口。"""
        WIN_BALL, WIN_CHAT = self.b["WIN_BALL"], self.b["WIN_CHAT"]
        files = (event.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        if not paths:
            # 无真实路径（理论上不会发生，见 util.py 配对逻辑），给个提示
            self.controller.notify(WIN_BALL, "file-drop-state",
                                   {"state": "error", "message": "无法获取文件路径"})
            return

        results = []
        self.controller.notify(WIN_BALL, "file-drop-state",
                               {"state": "processing", "message": f"正在处理 {len(paths)} 个文件…"})
        for p in paths:
            try:
                results.append(self.api.process_file(p))
            except Exception as exc:  # noqa: BLE001
                results.append({"ok": False, "name": os.path.basename(p),
                                "message": "", "error": str(exc)})

        ok_count = sum(1 for r in results if r.get("ok"))
        summary = f"文件处理完成：成功 {ok_count}/{len(results)}"
        self.controller.notify(WIN_BALL, "file-drop-state",
                               {"state": "done", "message": summary, "results": results})
        # 同时把摘要与失败明细推送到聊天窗 toast
        self.controller.notify(WIN_CHAT, "backend-toast",
                               {"type": "success" if ok_count == len(results) else "warning",
                                "text": summary})
        for r in results:
            if not r.get("ok"):
                self.controller.notify(WIN_CHAT, "backend-toast",
                                       {"type": "error",
                                        "text": f"{r.get('name')}：{r.get('error')}"})

    # ------------------------------------------------------------------ #
    # 托盘
    # ------------------------------------------------------------------ #
    def start_tray(self):
        """启动系统托盘；失败仅告警并降级为全局热键控制，不阻断主程序。"""
        try:
            self._start_tray_impl()
        except Exception as exc:  # noqa: BLE001
            log.exception("系统托盘启动失败，仅使用全局热键控制窗口")
            print(f"[警告] 系统托盘启动失败（{exc}），可通过全局热键控制窗口。")

    def _start_tray_impl(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError as exc:
            log.warning("无法启动系统托盘（缺少依赖 %s），可通过全局热键控制窗口。", exc)
            return

        WIN_BALL, WIN_CHAT = self.b["WIN_BALL"], self.b["WIN_CHAT"]
        controller, settings = self.controller, self.settings

        def _toggle(wid):
            controller.toggle(wid)

        # --- 主题子菜单 ---
        def _themes_menu():
            import pystray as _pt

            themes = self.api.get_themes() or []

            def _apply(theme_id):
                try:
                    theme = self.api.apply_theme(theme_id)
                    controller.notify(WIN_CHAT, "theme-changed", theme)
                    controller.notify(WIN_BALL, "theme-changed", theme)
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
            pystray.MenuItem("显示/隐藏 悬浮球", lambda _i, _it: _toggle(WIN_BALL)),
            pystray.MenuItem("显示/隐藏 悬浮窗", lambda _i, _it: _toggle(WIN_CHAT)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("切换主题", _themes_menu()),
            pystray.MenuItem("切换 AI 接口", _providers_menu()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出程序", lambda _i, _it: self.quit()),
        )

        image = self._make_tray_icon()

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
        try:
            import keyboard
        except ImportError:
            log.warning("keyboard 不可用，全局热键未注册。")
            return

        WIN_BALL, WIN_CHAT = self.b["WIN_BALL"], self.b["WIN_CHAT"]
        hotkeys = self.settings.get("hotkeys") or {}

        def _make_toggle(wid):
            def _cb():
                try:
                    self.controller.toggle(wid)
                except Exception as exc:  # noqa: BLE001
                    log.warning("热键切换 %s 失败: %s", wid, exc)

            return _cb

        for key, wid in (
            (hotkeys.get("toggle_ball"), WIN_BALL),
            (hotkeys.get("toggle_chat"), WIN_CHAT),
        ):
            if not key:
                continue
            try:
                handle = keyboard.add_hotkey(key, _make_toggle(wid))
                self._hotkey_handles.append((handle, key))
                log.info("全局热键已注册: %s -> %s", key, wid)
            except Exception as exc:  # noqa: BLE001
                log.warning("热键 %s 注册失败（可能被占用或权限不足）: %s", key, exc)

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
        for wid in (self.b["WIN_BALL"], self.b["WIN_CHAT"]):
            self.controller.remember_position(wid)
        self.controller.destroy_all()
        # 兜底：若 GUI 循环未能自然退出，4 秒后强制结束进程
        threading.Timer(4.0, os._exit, args=[0]).start()

    # ------------------------------------------------------------------ #
    def _on_gui_started(self):
        """GUI 循环启动后（子线程）：
        1) 隐藏任务栏图标；
        2) 让窗口从此可被点击激活（启动早期 focus=False 会令 pywebview
           在每次 Activated 事件里反复重加 WS_EX_NOACTIVATE —— 把 focus 标记
           翻为 True 即可让它不再重加，配合 enable_activation 清除现有样式）；
        3) 窗口激活时强制给 WebView2 控件焦点（键盘直达，等价隐藏/再显示的路径）。
        """
        import time

        win_ids = (self.b["WIN_BALL"], self.b["WIN_CHAT"])
        deadline = time.time() + 10
        while time.time() < deadline:
            if all(self.controller.window_handle(w) is not None for w in win_ids):
                break
            time.sleep(0.2)
        for wid in win_ids:
            try:
                # 关键：解除 pywebview 对 focus=False 的持续重加
                win = self.windows.get(wid)
                if win is not None and hasattr(win, "focus"):
                    win.focus = True
                ok = self.controller.hide_taskbar_button(wid)
                log.info("隐藏任务栏图标 %s -> %s", wid, ok)
            except Exception as exc:  # noqa: BLE001
                log.warning("隐藏任务栏图标失败(%s): %s", wid, exc)
            try:
                self.controller.enable_activation(wid)
            except Exception as exc:  # noqa: BLE001
                log.warning("恢复窗口激活(%s)失败: %s", wid, exc)
            try:
                # 以系统真实可见性校正内部记录（聊天窗可能启动即可见）
                self.controller.sync_visibility(wid)
            except Exception as exc:  # noqa: BLE001
                log.warning("可见性校正(%s)失败: %s", wid, exc)
            try:
                self.controller.focus_webview_on_activate(wid)
            except Exception as exc:  # noqa: BLE001
                log.warning("挂接 WebView 聚焦(%s)失败: %s", wid, exc)
        # 悬浮球启动即显示、但透明合成需一次“隐藏→重显”才建立：
        # 启动约 1.5s 后自动预热一次，消除启动时的白底
        try:
            self.controller.arm_startup_transparency()
        except Exception as exc:  # noqa: BLE001
            log.warning("悬浮球启动预热调度失败: %s", exc)

    # ------------------------------------------------------------------ #
    def run(self):
        import webview

        self.build_windows()
        # 悬浮球注册文件拖放
        self.controller.register_drop_handler(self.b["WIN_BALL"], self._on_files_dropped)
        self.start_hotkeys()
        self.start_tray()

        debug = os.environ.get("ALICE_DEBUG") == "1"
        log.info("进入 GUI 事件循环（debug=%s）", debug)
        try:
            webview.start(func=self._on_gui_started, gui="edgechromium", debug=debug)
        except Exception as exc:  # noqa: BLE001
            log.exception("GUI 启动失败")
            print(f"\n[错误] GUI 启动失败：{exc}")
            print("请确认已安装 WebView2 Runtime（Win10/11 + Edge 一般自带）。")
            self.quit()
            return 1
        log.info("程序已退出。")
        return 0


def main() -> int:
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

# -*- coding: utf-8 -*-
"""
窗口控制器：封装两个 pywebview 窗口（悬浮球 / 悬浮窗）的统一操作。

* 供托盘菜单、全局热键（任意线程）以及前端 API 调用；
* pywebview 6.x WinForms 后端内部通过 Invoke 封送到 GUI 线程，
  因此跨线程调用 show/hide/move/resize 是安全的；这里仍统一 try/except 兜底；
* 维护窗口位置记忆（moved/resized 事件触发时更新内存，隐藏/退出时落盘）。

本模块不 import webview，保持可无头导入。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable

from core.logger import get_logger
from core import window_probe as probe

log = get_logger(__name__)

WIN_BALL = "ball"
WIN_CHAT = "chat"


class WindowController:
    def __init__(self, settings_manager):
        self._settings = settings_manager
        self._windows: dict[str, Any] = {}
        # 内存中的窗口几何信息（逻辑像素，落盘前不写文件）
        self._geometry: dict[str, dict] = {WIN_BALL: {}, WIN_CHAT: {}}
        self._shown: dict[str, bool] = {WIN_BALL: False, WIN_CHAT: False}
        self._focus_hooked: set[str] = set()
        # resize 后透明恢复：去抖定时器 / 重显定时器 / 防递归时间戳
        self._refresh_debounce: dict[str, threading.Timer] = {}
        self._refresh_show: dict[str, threading.Timer] = {}
        self._refresh_pending: dict[str, bool] = {}   # 恢复周期进行中标记
        self._last_refresh: dict[str, float] = {}
        # 恢复方式：smart=先试无闪烁手段并实测验证、无效再兜底闪烁（默认）
        #           blink=直接隐藏→重显（一定有效，有闪烁）
        #           soft =仅重设背景色（对照用，已知无效）
        # 环境变量 ALICE_REFRESH_MODE 可覆盖（便于对照测试）
        self._refresh_mode = os.environ.get("ALICE_REFRESH_MODE", "smart")
        # 无闪烁手段本进程内的实测结论：unknown / ok / failed
        self._quiet_state = "unknown"
        self._quiet_stage = None          # 上次成功的无闪烁手段名
        self._quiet_dead: set[str] = set()  # 本进程内已确认无效的手段
        # 悬浮球启动预热标记：启动即显示的透明窗口需一次“隐藏→重显”建立透明
        self._ball_primed = False
        # 位置/尺寸落盘去抖定时器
        self._save_timers: dict[str, threading.Timer] = {}

    # ------------------------------------------------------------------ #
    def register(self, window_id: str, window: Any, initially_visible: bool = True) -> None:
        """注册窗口并挂接位置记忆事件。"""
        self._windows[window_id] = window
        self._shown[window_id] = initially_visible

        def _on_moved():
            self._remember(window_id)
            self._schedule_save(window_id)

        def _on_resized():
            self._remember(window_id)
            self._schedule_save(window_id)
            # WebView2 透明合成在调整大小后会失效（整窗变白底直角），
            # 等尺寸稳定后自动做一次“隐形重显”恢复（见 refresh_transparency）
            self._schedule_refresh(window_id)

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
            # 每次显示都顺带重申“不显示任务栏图标”（WinForms 的 Show 可能重置该样式）
            if os.name == "nt":
                try:
                    self.hide_taskbar_button(window_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("隐藏任务栏按钮失败(%s): %s", window_id, exc)
                # 透明悬浮窗：显式关闭 Win11 的 DWM 系统背景（Mica 等），
                # 防止调整窗口大小后系统给窗口画上不透明背景层
                try:
                    self.disable_system_backdrop(window_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("关闭系统背景(%s)失败: %s", window_id, exc)
            # 悬浮球：启动即显示但透明合成未建立（背景发白），首次显示后预热一次
            if window_id == WIN_BALL and not self._ball_primed:
                t = threading.Timer(0.6, self._prime_ball)
                t.daemon = True
                t.start()
        except Exception as exc:  # noqa: BLE001
            log.warning("show(%s) 失败: %s", window_id, exc)

    def hide(self, window_id: str) -> None:
        win = self._window(window_id)
        if not self._ready(win):
            return
        try:
            # 用户主动隐藏：取消挂起的“隐藏→重显”恢复，避免恢复周期把窗口弹回来
            self._refresh_pending.pop(window_id, None)
            t = self._refresh_show.pop(window_id, None)
            if t is not None:
                try:
                    t.cancel()
                except Exception:  # noqa: BLE001
                    pass
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
        """启动后校正：以系统真实可见性为准（聊天窗可能启动即可见），
        并记录一次“正常状态”的窗口样式快照，便于与破损状态对比。"""
        vis = self._os_visible(window_id)
        if vis is not None:
            self._shown[window_id] = vis
            log.info("窗口可见性校正 %s -> %s", window_id, vis)
        try:
            log.info("窗口初始快照 %s: %s", window_id,
                     probe.describe(self.window_handle(window_id)))
        except Exception as exc:  # noqa: BLE001
            log.debug("窗口快照失败(%s): %s", window_id, exc)

    def _prime_ball(self) -> None:
        """悬浮球一次性预热：启动即显示的透明窗口需要一次重建才能透出桌面。
        走与聊天窗相同的 smart 恢复（先试无闪烁手段，实测无效才闪烁）。"""
        if self._ball_primed:
            return
        if WIN_BALL not in self._windows:
            return
        if self._os_visible(WIN_BALL) is not True:
            return  # 球不可见时不做；稍后首次显示时 show() 会再次调度
        self._ball_primed = True
        log.info("悬浮球启动预热：开始恢复透明合成")
        self.refresh_transparency(WIN_BALL)

    def arm_startup_transparency(self, delay: float = 1.5) -> None:
        """GUI 启动后调用：悬浮球启动即显示、但透明合成未建立（背景发白），
        延时后做一次性“隐藏→重显”预热。"""
        if os.name != "nt":
            return

        def _go():
            try:
                self._prime_ball()
            except Exception as exc:  # noqa: BLE001
                log.warning("悬浮球启动预热失败: %s", exc)

        t = threading.Timer(delay, _go)
        t.daemon = True
        t.start()

    def is_visible(self, window_id: str) -> bool:
        """基于内部状态判断可见性（pywebview 未暴露动态 visible 属性）。"""
        return bool(self._shown.get(window_id, False))

    def move(self, window_id: str, x: int, y: int) -> None:
        self._window(window_id).move(int(x), int(y))

    def resize(self, window_id: str, width: int, height: int) -> None:
        self._window(window_id).resize(int(width), int(height))

    # ------------------------------------------------------------------ #
    # resize 后 WebView2 透明背景失效的恢复（“隐藏→重显”）
    # ------------------------------------------------------------------ #
    def _schedule_refresh(self, window_id: str) -> None:
        """resize 事件去抖调度恢复；带防递归冷却，避免自身 hide/show
        引发的 resize 事件再次触发，形成“窗口抽搐”循环。"""
        if os.name != "nt" or window_id != WIN_CHAT:
            return  # 只有可被用户调整大小的透明聊天窗需要
        now = time.monotonic()
        last = self._last_refresh.get(window_id, 0.0)
        if now - last < 1.2:
            log.info("跳过恢复调度（冷却期内 %s）: %s", round(now - last, 2), window_id)
            return  # 我们自己隐藏/重显刚结束，忽略其引发的 resize
        t = self._refresh_debounce.pop(window_id, None)
        if t is not None:
            try:
                t.cancel()
            except Exception:  # noqa: BLE001
                pass
        t = threading.Timer(0.25, self._do_refresh_transparency, args=[window_id])
        t.daemon = True
        self._refresh_debounce[window_id] = t
        t.start()
        log.info("已调度透明恢复（resized 事件）: %s", window_id)

    def _do_refresh_transparency(self, window_id: str) -> None:
        self._refresh_debounce.pop(window_id, None)
        # 前端（缩放手柄 mouseup）可能刚刚已经修过：冷却期内直接忽略，
        # 否则会出现“两个修复线程并发 → 连续切换两次 → 看起来闪几下”
        elapsed = time.monotonic() - self._last_refresh.get(window_id, 0.0)
        if elapsed < 1.2:
            log.info("跳过 resized 触发的恢复（%.2fs 前已修复）: %s", elapsed, window_id)
            return
        log.info("resized 事件触发恢复: %s", window_id)
        self.refresh_transparency(window_id)

    def _gui_call(self, native: Any, fn: Callable[[], None]) -> bool:
        """把 fn 封送到 GUI 线程执行（WinForms Control.Invoke）。"""
        try:
            from System import Func  # noqa: PLC0415
            from System import Type as _SysType  # noqa: PLC0415

            native.Invoke(Func[_SysType](fn))
            return True
        except Exception:  # noqa: BLE001
            try:
                fn()
                return True
            except Exception:  # noqa: BLE001
                return False

    # ------------------------------------------------------------------ #
    # 透明状态探测
    # ------------------------------------------------------------------ #
    def _form_backcolor(self, native: Any):
        """读取 WinForms 窗体背景色（破损时环上就是这个颜色）。"""
        try:
            import clr  # noqa: PLC0415

            clr.AddReference("System.Drawing")
            color = native.BackColor
            return (int(color.R), int(color.G), int(color.B))
        except Exception:  # noqa: BLE001
            return None

    def _webview_control(self, native: Any):
        return getattr(native, "webview", None)

    def _webview_controller(self, wv: Any):
        """
        取 WebView2 的 CoreWebView2Controller。

        WinForms 控件把它放在**私有字段** `_coreWebView2Controller`，
        没有公开属性，因此需要反射；拿不到时返回 None（不影响其它阶段）。
        """
        if wv is None:
            return None
        try:
            from System.Reflection import BindingFlags  # noqa: PLC0415

            flags = (BindingFlags.Instance | BindingFlags.NonPublic
                     | BindingFlags.Public)
            ctype = wv.GetType()
            field = ctype.GetField("_coreWebView2Controller", flags)
            if field is not None:
                ctrl = field.GetValue(wv)
                if ctrl is not None:
                    return ctrl
            prop = ctype.GetProperty("CoreWebView2Controller", flags)
            if prop is not None:
                ctrl = prop.GetValue(wv)
                if ctrl is not None:
                    return ctrl
        except Exception as exc:  # noqa: BLE001
            log.info("反射获取 WebView2 控制器失败: %s", exc)
        return None

    def probe_transparency(self, window_id: str):
        """实测窗口“透明环”是否破损；返回 probe 结果 dict（不可判定时 verdict=unknown）。"""
        hwnd = self.window_handle(window_id)
        if not hwnd:
            return {"verdict": "unknown", "reason": "无窗口句柄"}
        win = self._windows.get(window_id)
        native = getattr(win, "native", None) if win else None
        ref = self._form_backcolor(native) if native is not None else None
        try:
            return probe.probe_window(hwnd, ref)
        except Exception as exc:  # noqa: BLE001
            return {"verdict": "unknown", "reason": f"探测异常: {exc}"}

    # ------------------------------------------------------------------ #
    # 透明恢复：先试“无闪烁”手段，实测无效再兜底闪烁
    # ------------------------------------------------------------------ #
    def refresh_transparency(self, window_id: str) -> bool:
        """
        恢复窗口透明（Windows / edgechromium 专用）。

        破损表现：调整大小后窗口四周本应透出桌面的边距被 WinForms
        窗体背景色填满（整窗“白底直角”）——实测可知失去的是**窗口级
        逐像素合成**，网页内容本身仍是透明的。

        恢复策略（self._refresh_mode，可用环境变量 ALICE_REFRESH_MODE 覆盖）：

          smart（默认）——先依次尝试若干**无任何视觉变化**的修复手段，
                          每步之后用 probe 实测“环上是否还是窗体背景色”，
                          一旦恢复正常立即结束；若全部无效才兜底做一次
                          隐藏→重显（有一瞬闪烁，但一定有效）。
                          同一进程内若确认无闪烁手段无效，后续直接走闪烁，
                          不再让用户白等。

          blink        —— 直接隐藏→重显（已验证有效）。
          soft         —— 仅重设背景色（已知无效，留作对照）。

        窗口不可见时跳过，绝不强行弹出；1.2s 冷却避免自身动作递归触发。
        """
        if os.name != "nt":
            return False
        win = self._windows.get(window_id)
        if win is None:
            log.warning("透明恢复：未知窗口 %s", window_id)
            return False
        native = getattr(win, "native", None)
        if native is None:
            log.warning("透明恢复：原生窗口未就绪 %s", window_id)
            return False
        os_vis = self._os_visible(window_id)
        if os_vis is False:
            log.info("透明恢复：窗口当前不可见，跳过 %s", window_id)
            return False
        if os_vis is None and not self._shown.get(window_id, False):
            log.info("透明恢复：无法确认可见，按记录跳过 %s", window_id)
            return False

        t2 = self._refresh_show.pop(window_id, None)
        if t2 is not None:
            try:
                t2.cancel()
            except Exception:  # noqa: BLE001
                pass

        # 同一窗口同一时刻只允许一轮修复：并发修复会连续切换两次可见性，
        # 用户看到的“闪几下”就是这么来的
        if self._refresh_pending.get(window_id):
            log.info("上一轮透明修复尚未结束，忽略重复请求: %s", window_id)
            return False

        self._last_refresh[window_id] = time.monotonic()
        self._refresh_pending[window_id] = True

        mode = self._refresh_mode
        if mode == "blink":
            return self._blink_once(window_id, native)

        if mode == "soft":
            def _soft():
                self._apply_soft(native, window_id)

            ok = self._gui_call(native, _soft)
            self._refresh_pending.pop(window_id, None)
            log.info("软恢复（对照模式）%s: %s", "已执行" if ok else "失败", window_id)
            return ok

        # smart：本进程内已确认“无闪烁手段无效”时，先做一次廉价探测，
        # 若当前其实没破损就什么都不做（避免无谓闪烁）
        if self._quiet_state == "failed":
            verdict = self.probe_transparency(window_id)
            if verdict.get("verdict") == "ok":
                log.info("透明环正常，无需恢复: %s", window_id)
                self._refresh_pending.pop(window_id, None)
                return True
            log.info("无闪烁手段本进程已确认无效，直接闪烁恢复: %s", window_id)
            return self._blink_once(window_id, native)

        # smart：后台线程里逐步尝试 + 实测验证
        th = threading.Thread(target=self._smart_repair, args=[window_id],
                              name=f"alice-repair-{window_id}", daemon=True)
        th.start()
        return True

    def _blink_once(self, window_id: str, native: Any) -> bool:
        """兜底：隐藏 → 250ms → Show + Activate（已验证有效，有一瞬闪烁）。"""
        def _hide():
            try:
                native.Hide()
            except Exception:  # noqa: BLE001
                pass
            t = threading.Timer(0.25, self._do_show_after_refresh, args=[window_id])
            t.daemon = True
            self._refresh_show[window_id] = t
            t.start()

        if self._gui_call(native, _hide):
            log.info("已触发隐藏→重显恢复（兜底）: %s", window_id)
            return True
        log.warning("透明恢复触发失败: %s", window_id)
        self._refresh_pending.pop(window_id, None)
        return False

    # ---------- 各“无闪烁”修复手段（均不改变窗口可见性/位置） ---------- #
    def _stage_bounds_nudge(self, native: Any) -> bool:
        """① 把 WebView 的 Bounds 原值 ±1px 再复原：强制重建合成表面，
        不改变可见性、不做隐藏（理论上无任何可见变化）。"""
        ctrl = self._webview_controller(self._webview_control(native))
        if ctrl is None:
            return False
        try:
            baseline = ctrl.Bounds

            def _nudge():
                try:
                    rect = ctrl.Bounds
                    widened = type(rect)(rect.X, rect.Y, rect.Width + 1, rect.Height + 1)
                    ctrl.Bounds = widened
                    ctrl.Bounds = rect
                except Exception as exc:  # noqa: BLE001
                    log.info("bounds_nudge 内部失败: %s", exc)

            if not self._gui_call(native, _nudge):
                return False
            log.info("bounds_nudge 原尺寸: %s", baseline)
            return True
        except Exception as exc:  # noqa: BLE001
            log.info("bounds_nudge 不可用: %s", exc)
            return False

    def _stage_controller_bg(self, native: Any) -> bool:
        """② 把控制器（而非控件）的默认背景色重新设为透明。

        WinForms 控件的同名属性是“建控件时用”的（改它无效，这正是早期
        软恢复失败的原因）；控制器上的属性才是初始化后可改的那个。
        """
        ctrl = self._webview_controller(self._webview_control(native))
        if ctrl is None:
            log.info("controller_bg 不可用: 未取到 WebView2 控制器")
            return False
        try:
            import clr  # noqa: PLC0415

            clr.AddReference("System.Drawing")
            from System.Drawing import Color  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            log.info("controller_bg 不可用: 载入 System.Drawing 失败 %s", exc)
            return False

        result = False
        targets = [("controller", ctrl), ("control", self._webview_control(native))]
        for label, target in targets:
            if target is None:
                continue
            for value_name, value in (("Transparent", Color.Transparent),
                                      ("ARGB(0,0,0,0)", Color.FromArgb(0, 0, 0, 0))):
                try:
                    target.DefaultBackgroundColor = value
                    log.info("controller_bg: %s.DefaultBackgroundColor = %s 已设置",
                             label, value_name)
                    result = True
                    break
                except Exception as exc:  # noqa: BLE001
                    log.info("controller_bg: 设置 %s -> %s 失败: %s", label, value_name, exc)
        try:
            native.Invalidate(True)
            native.Update()
        except Exception:  # noqa: BLE001
            pass
        return result

    def _stage_visible_noop(self, native: Any) -> bool:
        """③ 重设一次 WebView 的 IsVisible（值不变）：若重建合成只由
        属性 setter 触发，则这一步完全不产生任何视觉变化。"""
        ctrl = self._webview_controller(self._webview_control(native))
        if ctrl is None:
            return False
        try:
            current = bool(ctrl.IsVisible)

            def _reset():
                try:
                    ctrl.IsVisible = current
                except Exception as exc:  # noqa: BLE001
                    log.info("visible_noop 失败: %s", exc)

            self._gui_call(native, _reset)
            return True
        except Exception as exc:  # noqa: BLE001
            log.info("visible_noop 不可用: %s", exc)
            return False

    def _stage_webview_visible(self, native: Any, gap: float) -> bool:
        """④ 只切换 WebView 自身可见性（窗口不隐藏、位置不变）。"""
        ctrl = self._webview_controller(self._webview_control(native))
        if ctrl is None:
            return False
        try:
            def _hide():
                try:
                    ctrl.IsVisible = False
                except Exception:  # noqa: BLE001
                    pass

            def _show():
                try:
                    ctrl.IsVisible = True
                except Exception:  # noqa: BLE001
                    pass

            if not self._gui_call(native, _hide):
                return False
            if gap > 0:
                time.sleep(gap)
            self._gui_call(native, _show)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _stage_window_tick(self, native: Any) -> bool:
        """③ 同一个 GUI 消息回合内 Hide→Show（理论上不渲染中间帧）。"""
        def _tick():
            try:
                native.Hide()
                native.Show()
            except Exception:  # noqa: BLE001
                pass

        return self._gui_call(native, _tick)

    def _stage_child_tick(self, native: Any, gap: float) -> bool:
        """④ 只切换 WebView2 子窗口句柄的可见性（窗口与位置都不动）。"""
        wv = self._webview_control(native)
        if wv is None:
            return False
        try:
            handle = wv.Handle
            try:
                hwnd = int(handle)
            except (TypeError, ValueError):
                hwnd = 0
                for method in ("ToInt64", "ToInt32"):
                    fn = getattr(handle, method, None)
                    if fn is not None:
                        hwnd = int(fn())
                        break
        except Exception:  # noqa: BLE001
            return False
        if not hwnd:
            return False
        try:
            import ctypes  # noqa: PLC0415

            user32 = ctypes.windll.user32
            SW_HIDE, SW_SHOW = 0, 5

            def _hide():
                user32.ShowWindow(ctypes.c_void_p(hwnd), SW_HIDE)

            def _show():
                user32.ShowWindow(ctypes.c_void_p(hwnd), SW_SHOW)

            self._gui_call(native, _hide)
            if gap > 0:
                time.sleep(gap)
            self._gui_call(native, _show)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _smart_repair(self, window_id: str) -> None:
        """
        逐步尝试无闪烁修复，每步实测；全部无效则兜底闪烁。

        成功的手段会记在 self._quiet_stage 里，下次直接用。
        """
        win = self._windows.get(window_id)
        native = getattr(win, "native", None) if win else None
        if native is None:
            self._refresh_pending.pop(window_id, None)
            return

        blink_started = False
        try:
            hwnd = self.window_handle(window_id)
            log.info("透明修复开始 %s | 修复前状态: %s", window_id,
                     probe.describe(hwnd))
            before = self.probe_transparency(window_id)
            log.info("修复前判定 %s: %s", window_id, before)
            if before.get("verdict") == "ok":
                log.info("透明环正常，无需修复: %s", window_id)
                self._quiet_state = "ok"
                self._refresh_pending.pop(window_id, None)
                return

            stages_all = [
                # 先试“零视觉变化”的手段（若其中之一有效，就完全不会闪）
                ("bounds_nudge", lambda: self._stage_bounds_nudge(native), 0.15),
                ("controller_bg", lambda: self._stage_controller_bg(native), 0.15),
                ("visible_noop", lambda: self._stage_visible_noop(native), 0.15),
                # 已知有效但会有轻微闪烁（WebView 短暂不可见 → 露出窗体底色）
                ("webview_visible_tick",
                 lambda: self._stage_webview_visible(native, 0.0), 0.18),
                ("window_tick", lambda: self._stage_window_tick(native), 0.20),
                ("child_tick", lambda: self._stage_child_tick(native, 0.0), 0.15),
                # 最后两档：留出可见间隔的变体
                ("child_tick_gap50", lambda: self._stage_child_tick(native, 0.05), 0.25),
                ("webview_visible_gap150",
                 lambda: self._stage_webview_visible(native, 0.15), 0.25),
            ]
            # 已知有效的手段优先、已确认无效的不再重试：
            # 首次 resize 会把所有手段试一遍，之后只走最快那条路
            stages = [s for s in stages_all if s[0] == self._quiet_stage]
            stages += [s for s in stages_all
                       if s[0] != self._quiet_stage and s[0] not in self._quiet_dead]

            for name, action, settle in stages:
                if not self._refresh_pending.get(window_id):
                    log.info("修复中止（窗口已被隐藏）: %s", window_id)
                    return
                try:
                    applied = bool(action())
                except Exception as exc:  # noqa: BLE001
                    log.info("修复手段 %s 异常: %s", name, exc)
                    applied = False
                if not applied:
                    log.info("修复手段 %s 不可用，跳过", name)
                    self._quiet_dead.add(name)
                    continue
                time.sleep(settle)
                verdict = self.probe_transparency(window_id)
                log.info("修复手段 %s 后判定: %s", name, verdict)
                if verdict.get("verdict") != "ok":
                    self._quiet_dead.add(name)
                    continue

                # 该手段有效：记下来，下次优先后续只用这一条（最快、最不打扰）
                self._quiet_state = "ok"
                self._quiet_stage = name
                log.info("✅ 无闪烁修复成功（%s）: %s", name, window_id)

                # 安全兜底：确保 WebView 与窗口都处于可见状态，
                # 并重申工具窗口样式 / 关闭系统背景，再重绘一次
                def _ensure_visible():
                    try:
                        ctrl = self._webview_controller(self._webview_control(native))
                        if ctrl is not None:
                            try:
                                ctrl.IsVisible = True
                            except Exception:  # noqa: BLE001
                                pass
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        native.Invalidate(True)
                        native.Update()
                    except Exception:  # noqa: BLE001
                        pass

                self._gui_call(native, _ensure_visible)
                try:
                    self.hide_taskbar_button(window_id)
                    self.disable_system_backdrop(window_id)
                except Exception:  # noqa: BLE001
                    pass
                if (self._os_visible(window_id) is False
                        and self._refresh_pending.get(window_id)):
                    log.info("修复后窗口不可见，重新显示: %s", window_id)
                    self.show(window_id)
                self._refresh_pending.pop(window_id, None)
                return

            # 全部无闪烁手段无效 → 本轮会话内不再尝试，兜底闪烁
            self._quiet_state = "failed"
            log.info("⚠ 无闪烁手段均无效，回落到闪烁恢复: %s", window_id)
            blink_started = True
            self._blink_once(window_id, native)
        except Exception as exc:  # noqa: BLE001
            log.warning("透明修复异常(%s): %s", window_id, exc)
            blink_started = True
            self._blink_once(window_id, native)
        finally:
            # 未启动闪烁时本轮到此结束，释放“修复中”标记；
            # 启动闪烁时标记保留，由 _do_show_after_refresh 收尾释放
            if not blink_started:
                self._refresh_pending.pop(window_id, None)

    def _apply_soft(self, native: Any, window_id: str) -> None:
        """对照用：仅重设背景色 + 重绘（已知对“窗口级合成丢失”无效）。"""
        try:
            import clr  # noqa: PLC0415

            clr.AddReference("System.Drawing")
            from System.Drawing import Color  # noqa: PLC0415

            wv = self._webview_control(native)
            for target in (wv, self._webview_controller(wv)):
                if target is None:
                    continue
                try:
                    target.DefaultBackgroundColor = Color.Transparent
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        try:
            native.Invalidate(True)
            native.Update()
        except Exception:  # noqa: BLE001
            pass
        self.hide_taskbar_button(window_id)
        self.disable_system_backdrop(window_id)

    def _do_show_after_refresh(self, window_id: str) -> None:
        """重显阶段：Show + Activate，重申透明背景 / 工具窗口样式。"""
        self._refresh_show.pop(window_id, None)
        # 用户若在隐藏期间主动隐藏，hide() 已取消 pending → 不弹出
        if not self._refresh_pending.pop(window_id, None):
            log.info("用户已隐藏，取消自动重显: %s", window_id)
            return
        win = self._windows.get(window_id)
        if win is None:
            return
        native = getattr(win, "native", None)
        if native is None:
            return

        def _apply():
            # 与手动“显示”一致：先解除 NOACTIVATE，再 Show + Activate
            if hasattr(win, "focus"):
                win.focus = True
            try:
                self.enable_activation(window_id)
            except Exception:  # noqa: BLE001
                pass
            try:
                win.show()   # Show() + Activate()
            except Exception:  # noqa: BLE001
                try:
                    native.Show()
                except Exception:  # noqa: BLE001
                    pass
            try:
                import clr  # noqa: PLC0415

                clr.AddReference("System.Drawing")
                from System.Drawing import Color  # noqa: PLC0415

                wv = getattr(native, "webview", None)
                if wv is not None:
                    try:
                        wv.DefaultBackgroundColor = Color.Transparent
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass
            try:
                native.Invalidate(True)
                native.Update()
            except Exception:  # noqa: BLE001
                pass

        if self._gui_call(native, _apply):
            # Show 后重申“无任务栏按钮”样式与关闭系统背景，避免 WinForms/DWM 重置
            self.hide_taskbar_button(window_id)
            self.disable_system_backdrop(window_id)
            log.info("隐藏→重显完成: %s", window_id)

    # ------------------------------------------------------------------ #
    # 任务栏：把窗口标记为工具窗口（WS_EX_TOOLWINDOW），不显示任务栏按钮
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

    def disable_system_backdrop(self, window_id: str) -> bool:
        """
        关闭 Win11 的 DWM 系统窗口背景（仅 Windows，失败静默）。

        Win11 22H2+ 会按 DWMWA_SYSTEMBACKDROP_TYPE 给顶层窗口画
        系统背景（Mica / Acrylic 等），透明悬浮窗在调整大小后可能被
        重新画上一整块不透明背景层（“窗口背景变白”）。这里显式置为
        DWMSBT_NONE；Win10 / 不支持时 DwmSetWindowAttribute 返回
        错误，忽略即可。幂等。
        """
        if os.name != "nt":
            return False
        hwnd = self.window_handle(window_id)
        if not hwnd:
            return False
        try:
            import ctypes

            DWMWA_SYSTEMBACKDROP_TYPE = 38
            DWMSBT_NONE = 1
            val = ctypes.c_int(DWMSBT_NONE)
            hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_int(DWMWA_SYSTEMBACKDROP_TYPE),
                ctypes.byref(val),
                ctypes.sizeof(val),
            )
            if hr != 0:
                return False
            log.debug("已关闭 DWM 系统背景: %s", window_id)
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug("disable_system_backdrop(%s) 失败: %s", window_id, exc)
            return False

    def hide_taskbar_button(self, window_id: str) -> bool:
        """
        隐藏窗口的任务栏按钮（仅 Windows）。

        原理：给窗口扩展样式追加 WS_EX_TOOLWINDOW 并清除 WS_EX_APPWINDOW。
        工具窗口不会出现在任务栏与 Alt+Tab 中，是托盘常驻工具的常见做法。
        幂等，可重复调用。
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
    def remember_position(self, window_id: str) -> None:
        """把内存中的位置（chat 还含宽高）记忆落盘。"""
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

    # ------------------------------------------------------------------ #
    def register_drop_handler(self, window_id: str, handler: Callable[[dict], None]) -> bool:
        """
        用 pywebview DOM API 在窗口页面上注册 drop 事件监听，
        使系统拖入的文件能携带真实路径（event['dataTransfer']['files'][*]['pywebviewFullPath']）。

        返回是否注册成功。handler 在工作线程中被调用。
        """
        win = self._windows.get(window_id)
        if win is None:
            return False

        def _install():
            try:
                from webview.dom import DOMEventHandler  # type: ignore

                target = win.dom.get_element("body")
                if target is None:
                    log.warning("未能取得 %s 的 body 元素", window_id)
                    return
                target.on("drop", DOMEventHandler(handler, prevent_default=True))
                log.info("文件拖放监听已注册 -> %s", window_id)
            except Exception as exc:  # noqa: BLE001
                log.exception("注册文件拖放监听失败(%s): %s", window_id, exc)

        # 页面加载完成后再注册
        win.events.loaded += _install
        return True

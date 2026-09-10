# -*- coding: utf-8 -*-
"""
窗口状态探测与“透明破损”判定（纯 ctypes，可无头导入）。

背景
----
悬浮窗（frameless + transparent）在调整大小后偶发“背景变不透明”：
WebView2 的**窗口级透明合成**失效后，窗口四周本应透出桌面的边距
会被 WinForms 窗体自身的不透明背景色（SystemColors.Control，
浅色主题下为 #F0F0F0）填满，看起来就是“白底直角”。

因此“是否破损”可以**实测**出来：在窗口边距（透明环）上采样屏幕
像素——

    * 正常：环上是桌面内容，与窗口外侧紧邻的像素基本一致；
    * 破损：环上整圈都是窗体背景色，且与窗口外侧明显不同。

本模块只做只读探测，不修改任何窗口状态；供 window_controller 在
执行恢复动作前后做判定，从而“先试无闪烁方案、验证失败再兜底”。

约定：宁可把正常误判为破损（多一次恢复动作，无副作用），也不要
把破损漏判为正常（会留下白底）。
"""
from __future__ import annotations

import ctypes
from typing import Any

# ---- Win32 常量 ----
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000
LWA_COLORKEY = 0x00000001
LWA_ALPHA = 0x00000002

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _user32():
    return ctypes.windll.user32


def _get_long(hwnd: int, index: int):
    user32 = _user32()
    get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    try:
        get_long.restype = ctypes.c_ssize_t
        return int(get_long(ctypes.c_void_p(hwnd), ctypes.c_int(index)))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------- #
# 窗口属性快照（诊断用）
# ---------------------------------------------------------------------- #
def get_rect(hwnd: int):
    """返回窗口矩形 (left, top, right, bottom)，失败返回 None。"""
    if not hwnd:
        return None
    try:
        r = _RECT()
        if not _user32().GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
            return None
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:  # noqa: BLE001
        return None


def get_layered(hwnd: int):
    """返回分层窗口属性 dict，非分层窗口返回 None。"""
    if not hwnd or not (_get_long(hwnd, GWL_EXSTYLE) or 0) & WS_EX_LAYERED:
        return None
    try:
        flags = ctypes.c_uint(0)
        color = ctypes.c_uint(0)
        alpha = ctypes.c_ubyte(0)
        ok = _user32().GetLayeredWindowAttributes(
            ctypes.c_void_p(hwnd), ctypes.byref(color),
            ctypes.byref(alpha), ctypes.byref(flags),
        )
        if not ok:
            return None
        return {"flags": int(flags.value), "color": int(color.value), "alpha": int(alpha.value)}
    except Exception:  # noqa: BLE001
        return None


def is_visible(hwnd: int):
    if not hwnd:
        return None
    try:
        return bool(_user32().IsWindowVisible(ctypes.c_void_p(hwnd)))
    except Exception:  # noqa: BLE001
        return None


def describe(hwnd: int) -> dict[str, Any]:
    """窗口样式 / 分层属性 / 位置 / 可见性快照（写日志用）。"""
    if not hwnd:
        return {"hwnd": None}
    style = _get_long(hwnd, GWL_STYLE)
    ex = _get_long(hwnd, GWL_EXSTYLE)
    return {
        "hwnd": hwnd,
        "visible": is_visible(hwnd),
        "rect": get_rect(hwnd),
        "style": hex(style) if style is not None else None,
        "exstyle": hex(ex) if ex is not None else None,
        "layered": bool(ex is not None and ex & WS_EX_LAYERED),
        "layered_attrs": get_layered(hwnd),
        "toolwindow": bool(ex is not None and ex & WS_EX_TOOLWINDOW),
        "noactivate": bool(ex is not None and ex & WS_EX_NOACTIVATE),
    }


# ---------------------------------------------------------------------- #
# 透明环采样
# ---------------------------------------------------------------------- #
def _virtual_screen():
    try:
        u = _user32()
        return (u.GetSystemMetrics(SM_XVIRTUALSCREEN), u.GetSystemMetrics(SM_YVIRTUALSCREEN),
                u.GetSystemMetrics(SM_CXVIRTUALSCREEN), u.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    except Exception:  # noqa: BLE001
        return None


def ring_points(hwnd: int, insets=(4, 7), corner: int = 30,
                outsets=(8,)) -> list[tuple[int, int, str]]:
    """
    生成“透明环”采样点，返回 [(x, y, kind)]，kind 为 inside/outside。

    * inside ：窗口内侧 insets 像素处（应透出桌面；按 9px 边距设计，
                取 4/7 两个偏移，避开圆角区域）；
    * outside：窗口外侧 outsets 像素处（作为“桌面本身长什么样”的参照）。

    四条边各取 1/4、1/2、3/4 三处，并避开圆角区域（距角 corner 像素）。
    """
    rect = get_rect(hwnd)
    if not rect:
        return []
    left, top, right, bottom = rect
    width, height = right - left, bottom - top
    if width <= 4 * max(insets) or height <= 4 * max(insets):
        return []

    vs = _virtual_screen()
    points: list[tuple[int, int, str]] = []

    def add(x: int, y: int, kind: str):
        if vs:
            vx, vy, vw, vh = vs
            if not (vx <= x < vx + vw and vy <= y < vy + vh):
                return
            if x < 0 or y < 0:
                return
        points.append((int(x), int(y), kind))

    fractions = (0.25, 0.5, 0.75)
    for frac in fractions:
        fx = max(corner, min(width - corner, int(width * frac)))
        fy = max(corner, min(height - corner, int(height * frac)))
        x = left + fx
        y = top + fy
        for inset in insets:
            # 上 / 下 / 左 / 右 四条边
            add(x, top + inset, "inside")
            add(x, bottom - 1 - inset, "inside")
            add(left + inset, y, "inside")
            add(right - 1 - inset, y, "inside")
        for off in outsets:
            add(x, top - off, "outside")
            add(x, bottom + off, "outside")
            add(left - off, y, "outside")
            add(right + off, y, "outside")
    return points


def sample_rgb(points) -> list[tuple[int, int, str, tuple[int, int, int]]]:
    """在屏幕 DC 上取像素颜色（物理像素坐标）。"""
    if not points:
        return []
    user32 = _user32()
    gdi32 = ctypes.windll.gdi32
    try:
        # 64 位下 HDC 是 8 字节句柄，必须声明 restype/argtypes，否则会被截断
        user32.GetDC.restype = ctypes.c_void_p
        user32.GetDC.argtypes = [ctypes.c_void_p]
        user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.GetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        gdi32.GetPixel.restype = ctypes.c_uint
    except Exception:  # noqa: BLE001
        pass
    hdc = user32.GetDC(None)
    if not hdc:
        return []
    out = []
    try:
        for x, y, kind in points:
            value = gdi32.GetPixel(hdc, int(x), int(y))
            if value == 0xFFFFFFFF:  # CLR_INVALID
                continue
            out.append((int(x), int(y), kind,
                        (value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF)))
    except Exception:  # noqa: BLE001
        return []
    finally:
        try:
            user32.ReleaseDC(None, hdc)
        except Exception:  # noqa: BLE001
            pass
    return out


def _close(a, b, tol: int) -> bool:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2])) <= tol


def classify(samples, ref_rgb, tol: int = 4, edge_diff: int = 8) -> dict[str, Any]:
    """
    根据采样结果判定窗口透明是否破损。

    破损 = 绝大多数环上像素都等于窗体背景色 **且** 与窗口外侧的
    桌面像素明显不同（双重条件，尽量避免把“桌面恰好是灰色”误判）。

    :param samples: sample_rgb() 的返回
    :param ref_rgb: 窗体背景色 (r, g, b)，取不到时传 None
    """
    inside = [c for _, _, kind, c in samples if kind == "inside"]
    if not inside:
        return {"verdict": "unknown", "reason": "无环上采样点"}
    if ref_rgb is None:
        return {"verdict": "unknown", "reason": "无窗体背景色参照"}

    gray_hits = sum(1 for c in inside if _close(c, ref_rgb, tol))
    gray_ratio = gray_hits / len(inside)

    # 窗口外侧参照：按最近点配对，估算“桌面 vs 窗内”的颜色差
    outside = [(x, y, c) for x, y, kind, c in samples if kind == "outside"]
    diffs = []
    for ix, iy, kind, ic in samples:
        if kind != "inside":
            continue
        best = None
        for ox, oy, oc in outside:
            d2 = (ox - ix) ** 2 + (oy - iy) ** 2
            if best is None or d2 < best[0]:
                best = (d2, oc)
        if best is not None:
            diffs.append(max(abs(ic[i] - best[1][i]) for i in range(3)))
    diffs.sort()
    median_diff = diffs[len(diffs) // 2] if diffs else None

    if gray_ratio >= 0.9 and (median_diff is None or median_diff >= edge_diff):
        return {"verdict": "broken", "gray_ratio": round(gray_ratio, 2),
                "median_edge_diff": median_diff,
                "reason": "环上为窗体背景色且与窗外桌面不一致"}
    if gray_ratio <= 0.4:
        return {"verdict": "ok", "gray_ratio": round(gray_ratio, 2),
                "median_edge_diff": median_diff, "reason": "环上不是窗体背景色"}
    # 0.4 < gray_ratio < 0.9：既不像正常透出桌面、也没整圈变灰 —— 判定不明
    return {"verdict": "unknown", "gray_ratio": round(gray_ratio, 2),
            "median_edge_diff": median_diff, "reason": "环上采样不一致，状态不明"}


def probe_window(hwnd: int, ref_rgb) -> dict[str, Any]:
    """采样 + 判定，一步到位（附带采样点数量）。"""
    pts = ring_points(hwnd)
    samples = sample_rgb(pts)
    result = classify(samples, ref_rgb)
    result["samples"] = len(samples)
    result["inside"] = sum(1 for s in samples if s[2] == "inside")
    return result

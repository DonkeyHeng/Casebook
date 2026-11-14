"""窗口几何记忆 + 关闭检测(Windows 专用,ctypes 实现,无额外依赖)。

用途:
  1. 记住应用窗口上次的大小/位置/是否最大化,下次启动原样恢复;
  2. 用户点窗口右上角 ✕ 时,自动把后台服务也停掉(否则服务会一直挂着)。

为什么不用浏览器自己的记忆:实测 Edge 的 --app 窗口关闭重开后
尺寸会回到默认值,不保留。所以只能我们自己在窗口层面记。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import threading
import time

user32 = ctypes.windll.user32

SW_MAXIMIZE = 3
SWP_SHOWWINDOW = 0x0040
WM_CLOSE = 0x0010

EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


# ---------------------------------------------------------------- 基础操作


def find_window(keyword: str, class_name: str = "Chrome_WidgetWin_1") -> int:
    """找标题含 keyword、窗口类为浏览器窗口的顶层窗口。

    必须同时按窗口类过滤:否则标题里带 "Casebook" 的资源管理器窗口也会被误判成应用窗口。
    """
    hits: list[int] = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        if class_name:
            cls = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, cls, 128)
            if cls.value != class_name:
                return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if keyword in buf.value:
                hits.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return hits[0] if hits else 0


def get_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def is_maximized(hwnd: int) -> bool:
    return bool(user32.IsZoomed(hwnd))


def focus_window(hwnd: int) -> None:
    """把已有窗口提到前台,避免重复双击时开出一堆窗口。"""
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def read_state(hwnd: int) -> dict:
    x, y, w, h = get_rect(hwnd)
    return {"x": x, "y": y, "w": w, "h": h, "maximized": is_maximized(hwnd)}


def apply_state(hwnd: int, state: dict) -> None:
    """把窗口搬到上次的位置/尺寸。"""
    if not state:
        return
    if state.get("maximized"):
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        return
    w, h = int(state.get("w", 0)), int(state.get("h", 0))
    if w <= 100 or h <= 100:  # 记录不可信就别动
        return
    x, y = int(state.get("x", 80)), int(state.get("y", 80))
    user32.SetWindowPos(hwnd, 0, x, y, w, h, SWP_SHOWWINDOW)


# ---------------------------------------------------------------- 持久化


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(path: str, state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


# ---------------------------------------------------------------- 启动参数


def launch_args(exe: str, url: str, state: dict) -> list[str]:
    """把上次的几何翻译成浏览器启动参数(--app 模式)。"""
    args = [exe, f"--app={url}"]
    if state.get("maximized"):
        args.append("--start-maximized")
    elif state.get("w") and state.get("h"):
        args.append(f"--window-size={int(state['w'])},{int(state['h'])}")
        args.append(f"--window-position={int(state.get('x', 80))},{int(state.get('y', 80))}")
    return args


# ---------------------------------------------------------------- 监视


def watch(
    keyword: str,
    state_path: str,
    on_closed=None,
    find_timeout: float = 20.0,
    interval: float = 1.2,
    close_grace: float = 2.5,
    log=None,
) -> None:
    """后台线程:等待窗口出现 → 恢复上次几何 → 持续记录 → 窗口消失时回调。

    on_closed 在窗口被关掉约 close_grace 秒后触发一次(用于停服务)。
    """
    say = log or (lambda _m: None)
    hwnd = 0
    deadline = time.time() + find_timeout
    while time.time() < deadline:
        hwnd = find_window(keyword)
        if hwnd:
            break
        time.sleep(0.5)
    if not hwnd:
        say("[窗口] 没找到应用窗口(可能是普通标签页),不监视")
        return

    saved = load_state(state_path)
    if saved:
        apply_state(hwnd, saved)
        say(f"[窗口] 已恢复上次几何 {saved}")

    last = None
    missing_since = None
    while True:
        hwnd = find_window(keyword)
        if hwnd:
            missing_since = None
            current = read_state(hwnd)
            if current != last:
                last = current
                if current != saved:
                    save_state(state_path, current)
                    saved = current
                    say(f"[窗口] 记录几何 {current}")
        else:
            if missing_since is None:
                missing_since = time.time()
                say("[窗口] 窗口不见了,等待确认…")
            elif time.time() - missing_since >= close_grace:
                say("[窗口] 确认已关闭 → 停止服务")
                if on_closed:
                    try:
                        on_closed()
                    except Exception:
                        pass
                return
        time.sleep(interval)


def start_watch(keyword: str, state_path: str, on_closed=None, log=None) -> threading.Thread:
    th = threading.Thread(
        target=watch,
        args=(keyword, state_path, on_closed),
        kwargs={"log": log},
        name="window-watch",
        daemon=True,
    )
    th.start()
    return th

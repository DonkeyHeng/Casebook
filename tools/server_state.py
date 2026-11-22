"""判断本地服务是不是已经在跑 —— 给 start.cmd 用,**不连端口**。

为什么不连端口
--------------
`connect_ex` 连一个**没在监听**的本地端口,在装了网络过滤驱动(杀毒 / 防火墙)
的机器上不会立刻回 RST,而是要等 SYN 超时:本机实测 2.08 s(NET 的 TcpClient
同样 2.08 s,连监听中的端口只要 1 ms)。start.cmd 每次启动都要问一次"是不是
已经在跑",这一问就白等 2 s;等服务起来的循环里还要再问几次。

改成读服务自己写的 `data/server.pid`:进程还在、端口对得上,就算在跑。
服务端 `serve()` 是**先绑端口再写这个文件**,所以"文件在 + 进程在"等于
"端口已经能用",不会出现文件有、端口还没起来的空档。

判定条件(全部满足才算在跑)
--------------------------
1. `data/server.pid` 能读懂,pid 是正整数;
2. 该 pid 的进程还在;
3. 进程名像 Python / Casebook(防止 pid 被系统复用给别的程序);
4. 文件里记的端口与要查的端口一致。

退出码:0 = 在跑;1 = 没在跑(没有文件 / 进程已退出 / 端口不符)。

跑法:
    python tools/server_state.py --port 8000            # 只查一次
    python tools/server_state.py --port 8000 --wait 1   # 最多等 1 s(轮询)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
PID_PATH = _ROOT / "data" / "server.pid"

#: OpenProcess 的 PROCESS_QUERY_LIMITED_INFORMATION:只要查存活与映像名,
#: 用最小权限,免得不必要的提权提示
_QUERY_LIMITED = 0x1000
#: GetExitCodeProcess 对仍在运行的进程返回这个值
_STILL_ACTIVE = 259
#: 轮询间隔:比服务端绑端口(约 0.5 s)细一档,又不会把 CPU 拉起来
_POLL_SECONDS = 0.15


def read_entry() -> dict | None:
    """读 PID 文件;没有、读不懂或 pid 非法都返回 None。"""
    try:
        data = json.loads(PID_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        pid = int(data.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    return data if pid > 0 else None


def _open_process(pid: int):
    """拿一个只读的进程句柄;拿不到返回 None(进程不存在或没权限)。"""
    if sys.platform != "win32":  # pragma: no cover - 只在 Windows 上跑
        return None
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p  # 句柄是指针宽度,按 int 会被截断
    handle = kernel32.OpenProcess(_QUERY_LIMITED, False, pid)
    return handle or None


def process_alive(pid: int) -> bool:
    """进程还在不在。"""
    if sys.platform != "win32":  # pragma: no cover - 只为跨平台跑测试
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    import ctypes

    handle = _open_process(pid)
    if not handle:
        return False
    kernel32 = ctypes.windll.kernel32
    try:
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(handle)


def image_name(pid: int) -> str:
    """进程的可执行文件名(小写);查不到返回空串。"""
    if sys.platform != "win32":  # pragma: no cover - 只在 Windows 上跑
        return ""
    import ctypes

    handle = _open_process(pid)
    if not handle:
        return ""
    kernel32 = ctypes.windll.kernel32
    try:
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
        size = ctypes.c_ulong(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return Path(buf.value).name.lower()
    finally:
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(handle)


def server_running(port: int | None = None) -> bool:
    """按 PID 文件判断服务是否在跑;给了 port 还要求端口对得上。"""
    entry = read_entry()
    if entry is None:
        return False
    if port is not None:
        try:
            if int(entry.get("port") or 0) != int(port):
                return False
        except (TypeError, ValueError):
            return False
    pid = int(entry["pid"])
    if not process_alive(pid):
        return False
    # pid 会被系统复用:只按"进程在"会误判成"服务在",再开一个死页面。
    # 名字取不到(权限不足)时退回信任 PID 文件里的 pid。
    name = image_name(pid)
    return ("python" in name or "casebook" in name) if name else True


def wait_until_ready(port: int, seconds: float = 0.0) -> bool:
    """等到服务就绪,最多等 seconds 秒;0 表示只查一次。"""
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        if server_running(port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_SECONDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="判断 Casebook 服务是否已在运行(读 PID 文件)")
    parser.add_argument("--port", type=int, default=8000, help="要检查的端口(默认 8000)")
    parser.add_argument(
        "--wait", type=float, default=0.0, help="最多等这么多秒(0 = 只查一次,默认 0)"
    )
    parser.add_argument("--quiet", action="store_true", help="不打印结论,只看退出码")
    args = parser.parse_args(argv)

    running = wait_until_ready(args.port, args.wait)
    if not args.quiet:
        if running:
            print(f"服务已在运行(端口 {args.port})")
        else:
            print(f"端口 {args.port} 上没有运行中的服务")
    return 0 if running else 1


if __name__ == "__main__":
    raise SystemExit(main())

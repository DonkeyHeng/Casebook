"""停止 aspen-insight 服务(按端口找进程,确认是 python 才杀)。

由桌面的「停止 aspen-insight」快捷方式调用:
    pythonw.exe tools\\stop.py 8000

注意(pythonw 下的两个坑):
  1. 调用 netstat/tasklist/taskkill 这类控制台程序时,如果不加 CREATE_NO_WINDOW,
     会闪出一个黑窗口 —— 必须带上这个标志。
  2. 某些系统的 netstat 输出是本地编码(GBK),直接 text=True 解码可能抛异常,
     脚本就静默死掉、什么提示都没有 —— 所以强制 errors="replace" 并写日志。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
QUIET = "--quiet" in sys.argv
PORT = int(ARGS[0]) if ARGS else 8000

CREATE_NO_WINDOW = 0x08000000
LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "stop.log")


def _log(text: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}\n")
    except Exception:
        pass


def _run(cmd: list[str]) -> str:
    """跑一个控制台命令,不弹窗、解码不炸。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=10,
        )
    except Exception as exc:
        _log(f"{cmd[0]} 调用失败: {type(exc).__name__}: {exc}")
        return ""
    return proc.stdout.decode("utf-8", errors="replace")


def _msg(text: str, icon: int = 0x40) -> None:
    _log(text.replace("\n", " | "))
    if QUIET:
        return
    try:
        ctypes.windll.user32.MessageBoxW(0, text, "aspen-insight", icon)
    except Exception as exc:
        _log(f"弹窗失败: {type(exc).__name__}: {exc}")


def listening_pids(port: int) -> set[str]:
    """从 netstat 里找监听该端口的 PID。"""
    pids: set[str] = set()
    for line in _run(["netstat", "-ano"]).splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local, state, pid = parts[1], parts[3].upper(), parts[4]
        if local.endswith(f":{port}") and state == "LISTENING":
            pids.add(pid)
    return pids


def process_name(pid: str) -> str:
    out = _run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])
    out = out.strip()
    # 形如 "pythonw.exe","6688","Console","1","12,345 K"
    return out.split(",")[0].strip('"') if out else ""


def main() -> int:
    _log(f"停止请求:端口 {PORT}")
    pids = listening_pids(PORT)
    if not pids:
        _msg(f"端口 {PORT} 上没有运行中的服务。")
        return 0

    stopped, skipped = [], []
    for pid in pids:
        name = process_name(pid).lower()
        if "python" not in name:
            skipped.append(f"{pid} ({name or '未知'})")
            continue
        _run(["taskkill", "/PID", pid, "/F"])
        stopped.append(pid)

    if stopped:
        _msg(f"已停止服务(端口 {PORT},PID {', '.join(stopped)})。\n浏览器页面可以关掉了。")
    else:
        _msg(f"端口 {PORT} 被非 Python 进程占用,未处理:{', '.join(skipped)}", 0x30)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback

        _log("未捕获异常:\n" + traceback.format_exc())
        _msg("停止脚本出错,详情见 data\\stop.log", 0x10)
        raise SystemExit(1)

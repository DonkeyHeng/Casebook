"""停止 Casebook 服务(优先按 PID 文件,其次按端口找进程;确认是它才杀)。

由桌面的「停止 Casebook」快捷方式调用:
    pythonw.exe tools\\stop.py 8000

注意(pythonw 下的两个坑):
  1. 调用 netstat/tasklist/taskkill 这类控制台程序时,如果不加 CREATE_NO_WINDOW,
     会闪出一个黑窗口 —— 必须带上这个标志。
  2. 某些系统的 netstat 输出是本地编码(GBK),直接 text=True 解码可能抛异常,
     脚本就静默死掉、什么提示都没有 —— 所以强制 errors="replace" 并写日志。

"确认是它"这一条是后来补的:以前只凭"监听该端口的进程名里含 python"就
taskkill /F,同端口上别的 Python 程序会被误杀;而且不检查 taskkill 的返回码,
结束失败时界面照样提示"已停止"。现在:
  * 服务启动时把 (pid, port) 写进 data/server.pid,停止脚本优先用它;
  * 没有 PID 文件时按端口找进程,再核验完整命令行里带 app.api / casebook;
  * taskkill 返回码非 0 → 报失败;返回 0 也要等端口真的没人监听才算成功。
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
QUIET = "--quiet" in sys.argv
PORT = int(ARGS[0]) if ARGS else 8000

CREATE_NO_WINDOW = 0x08000000
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(_ROOT, "data", "stop.log")
PID_PATH = os.path.join(_ROOT, "data", "server.pid")

#: 命令行里出现这些词,才算"这条命令跑的是我们这个服务"
OWN_MARKERS = ("app.api", "app\\api", "app/api", "casebook")


def _log(text: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}\n")
    except Exception:
        pass


def _decode(raw: bytes) -> str:
    """控制台程序输出先按 UTF-8 解,失败再试系统本地编码(cp936 等)。

    直接 errors="replace" 能跑但会把中文提示变成乱码,写进 stop.log 就看不懂了。
    """
    for enc in ("utf-8", "mbcs"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _run_rc(cmd: list[str]) -> tuple[str, int | None]:
    """跑一个控制台命令,返回 (输出文本, 退出码)。退出码 None 表示没能启动。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=10,
        )
    except Exception as exc:
        _log(f"{cmd[0]} 调用失败: {type(exc).__name__}: {exc}")
        return "", None
    return _decode(proc.stdout), proc.returncode


def _run(cmd: list[str]) -> str:
    """跑一个控制台命令,只要输出。"""
    return _run_rc(cmd)[0]


def _msg(text: str, icon: int = 0x40) -> None:
    _log(text.replace("\n", " | "))
    if QUIET:
        return
    try:
        ctypes.windll.user32.MessageBoxW(0, text, "Casebook", icon)
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


def process_alive(pid: str) -> bool:
    """进程还在不在。用 tasklist 判,不依赖 netstat。

    netstat 在受限环境(沙箱 / 不同作业对象)里可能把监听套接字归到 PID 0,
    按端口找进程会一无所获 —— 那时 PID 文件里的信息就是唯一的线索。
    """
    if not pid.isdigit():
        return False
    out = _run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]).strip()
    # 找到进程时是一行 CSV(以引号开头);找不到时是一句本地化的
    # "信息: 没有运行的任务…" / "INFO: No tasks…",不能靠英文前缀判断
    return out.startswith('"')


def process_cmdline(pid: str) -> str:
    """取进程完整命令行;取不到返回空串。

    先试 powershell(Win10/11 都在),再退回 wmic(老系统 / 精简镜像)。
    """
    if not pid.isdigit():
        return ""
    ps = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
          f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"]
    out, rc = _run_rc(ps)
    if rc == 0 and out.strip():
        return out.strip()
    out, rc = _run_rc(["wmic", "process", "where", f"processid={pid}", "get", "commandline"])
    if rc == 0 and out.strip():
        # wmic 的输出带一行表头 "CommandLine"
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        lines = [ln for ln in lines if ln.lower() != "commandline"]
        if lines:
            return " ".join(lines)
    return ""


def looks_like_casebook(pid: str) -> bool:
    """确认这个 PID 真的是 Casebook 服务,而不是同端口上的别的 Python 程序。

    核验顺序:完整命令行(最可靠)→ 进程名含 python(兜底,并记日志)。
    命令行里既没有仓名也没有 app.api,就当它不是我们的 —— 宁可不动。
    """
    cmdline = process_cmdline(pid).lower()
    if cmdline:
        return any(marker in cmdline for marker in OWN_MARKERS)
    name = process_name(pid).lower()
    _log(f"PID {pid} 的命令行取不到(进程名 {name or '未知'}),只能按进程名判断")
    return "python" in name


def port_released(port: int, wait_s: float = 3.0) -> bool:
    """等端口真的没人监听(最多等 wait_s 秒)。"""
    deadline = time.time() + wait_s
    while True:
        if not listening_pids(port):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.2)


def pid_file_entry() -> dict | None:
    """读服务启动时写下的 PID 文件;没有、读不懂或 pid 非法都返回 None。"""
    try:
        with open(PID_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        pid = int(data.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    return data if pid > 0 else None


def _pick_target() -> tuple[str | None, str]:
    """决定该结束哪个 PID。返回 (pid, 说明)。pid 为 None 表示不该动。"""
    entry = pid_file_entry()
    if entry:
        pid = str(entry.get("pid"))
        port = int(entry.get("port") or 0)
        if port == PORT and process_alive(pid) and looks_like_casebook(pid):
            _log(f"PID 文件指向 {pid}(端口 {PORT}),直接用它")
            return pid, ""
        _log(f"PID 文件不可用或进程已退出({entry}),退回按端口查找")

    owners = listening_pids(PORT)
    if not owners:
        return None, f"端口 {PORT} 上没有运行中的服务。"
    for pid in sorted(owners):
        if looks_like_casebook(pid):
            return pid, ""
    detail = ", ".join(f"{p} ({process_name(p) or '未知'})" for p in sorted(owners))
    return None, (f"端口 {PORT} 被别的进程占用,看着不像 Casebook,未处理:{detail}\n"
                  "确认真的是残留的 Casebook 后再手动结束它。")


def main() -> int:
    _log(f"停止请求:端口 {PORT}")
    target, reason = _pick_target()
    if target is None:
        _msg(reason, 0x40 if "没有运行中的服务" in reason else 0x30)
        return 0

    out, rc = _run_rc(["taskkill", "/PID", target, "/F"])
    _log(f"taskkill {target} → 返回码 {rc};{out.strip()}")
    if rc != 0:
        _msg(f"结束进程 {target} 失败(taskkill 返回 {rc})。\n"
             f"{out.strip()}\n\n可能是权限不足:试试右键「以管理员身份运行」。", 0x10)
        return 1
    if process_alive(target):
        _msg(f"taskkill 报告成功,但进程 {target} 还在。\n"
             "可能还有另一个实例:稍等再点一次,或看 data\\stop.log。", 0x30)
        return 1
    if not port_released(PORT):
        _msg(f"已结束进程 {target},但端口 {PORT} 仍在监听。\n"
             "可能还有另一个实例:稍等再点一次,或看 data\\stop.log。", 0x30)
        return 1
    try:
        os.unlink(PID_PATH)
    except OSError:
        pass
    _msg(f"已停止服务(端口 {PORT},PID {target})。\n浏览器页面可以关掉了。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        import traceback

        _log("未捕获异常:\n" + traceback.format_exc())
        _msg("停止脚本出错,详情见 data\\stop.log", 0x10)
        raise SystemExit(1) from exc

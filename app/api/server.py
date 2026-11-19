"""本地 HTTP 服务:后端 API + 静态前端。

启动:
    python -m app.api                 # 默认 http://127.0.0.1:8000
    python -m app.api --port 8800 --db data/cases.db

接口清单在 `app/api/routes/__init__.py` 的两张表里(`ROUTES` / `CASE_ROUTES`)——
那是唯一来源,增删接口改表,不要在这里再抄一份。本文件只留:

  * 安全闸(`_guard`):所有写操作要带 `X-Casebook-Client` 头,挡掉任意网页借着
    本地服务改配置 / 发外网请求;
  * 响应(`_json` / `_error`)与静态文件(`_static`)、图标;
  * 查表分发(`_api` / `_api_cases` 与三个 do_*);
  * 启停、窗口、PID、示例库替换。

各域的处理逻辑在 `app/api/routes/` 的 mixin 里。

设计:
  * 只用标准库,不引第三方依赖(打包 exe 时省事)
  * 读 Aspen 的动作走 `app.api.read_job` 的后台线程:服务由 pythonw 启动、没有控制台,
    进度只能靠 `/api/read/status` 轮询回页面
  * 只读:除了"读工况"和"写这次的快照",不碰用户的模型
  * 每个请求单独开库,避免多线程共享连接
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app import paths
from app.api import window as win
from app.api.errors import BadBody, BadQuery
from app.api.router import match
from app.api.routes import (
    CASE_ROUTES,
    ROUTES,
    CaseRoutes,
    ExportRoutes,
    MetaRoutes,
    PipeRoutes,
    PumpRoutes,
)
from app.export.workbook import build_workbook  # noqa: F401 —— 自测从这里取
from app.store import db

# 只读资源(网页、图标)与可写数据(data/)分开:打包成 exe 后前者在临时解包目录,
# 后者必须在 exe 旁边,否则一关程序数据就没了(见 app/paths.py)
WEB_DIR = os.path.join(str(paths.resource_root()), "app", "web")
PROJECT_ROOT = str(paths.resource_root())
DEFAULT_DB = str(paths.data_dir("cases.db"))
DEFAULT_LOG = str(paths.data_dir("server.log"))
STATE_PATH = str(paths.data_dir("window.json"))
# 「停止 Casebook」优先读这个文件定位进程:比"按端口猜哪个 python 是它"可靠
PID_PATH = str(paths.data_dir("server.pid"))

# 桌面快捷方式用的图标,同时当作网页 favicon —— 应用窗口在任务栏里才会是这张脸
ICON_PATH = os.path.join(PROJECT_ROOT, "assets", "app-icon.ico")

# 页面标题(窗口标题也是它),用来定位应用窗口;改 <title> 时要同步改这里
WINDOW_KEY = "Casebook · 工况数据"

# 用「应用窗口模式」打开,窗口是浏览器给这个页面单独开的,
# 所以页面里的 window.close() 能把窗口真的关掉(普通标签页不行)。
BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

DETACHED = 0x00000008
CREATE_NEW_GROUP = 0x00000200


# 换示例库是"备份 + 校验 + 原子替换",两个请求同时来会把彼此的临时文件换掉
_SAMPLE_SWAP_LOCK = threading.Lock()


def open_ui(url: str, prefer_app_window: bool = True, state: dict | None = None) -> str:
    """打开界面。返回实际用的方式(app / tab)。"""
    if prefer_app_window:
        state = state or {}
        for exe in BROWSERS:
            if not os.path.isfile(exe):
                continue
            try:
                subprocess.Popen(
                    win.launch_args(exe, url, state),
                    creationflags=DETACHED | CREATE_NEW_GROUP,
                    close_fds=True,
                )
                return "app"
            except Exception:
                continue
    webbrowser.open(url)
    return "tab"


def _ensure_logging(log_path: str) -> None:
    """pythonw 启动时没有控制台(sys.stdout is None),把输出重定向到日志文件。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    directory = os.path.dirname(os.path.abspath(log_path))
    os.makedirs(directory, exist_ok=True)
    # 这个句柄要一直开着(接管 stdout/stderr),不能关进 with
    fh = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 =====\n")
    if sys.stdout is None:
        sys.stdout = fh
    if sys.stderr is None:
        sys.stderr = fh


def _say(msg: str) -> None:
    try:
        print(msg)
        sys.stdout.flush()
    except Exception:
        pass


def _alert(msg: str) -> None:
    """无控制台时的致命错误弹窗(免得双击后毫无反应)。"""
    if sys.stdout is not None:
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, msg, "Casebook", 0x10)
    except Exception:
        pass


class Handler(
    CaseRoutes,
    PumpRoutes,
    PipeRoutes,
    ExportRoutes,
    MetaRoutes,
    BaseHTTPRequestHandler,
):
    """本地服务的 HTTP 外壳:安全闸、响应、静态文件、查表分发。

    各域的处理逻辑在 `app/api/routes/` 的 mixin 里(按 URL 前缀分);这里只留
    与"怎么收发"有关的东西。路由表见 `app/api/routes/__init__.py`。
    """

    # POST body 上限:普通 JSON 接口 256 KB;标准表导入要传一份 CSV 全文,单独放宽。
    # 没有上限时,一个写错的 Content-Length 就能让进程先把内存吃光再报错。
    JSON_BODY_LIMIT = 256 * 1024
    STANDARDS_BODY_LIMIT = 16 * 1024 * 1024

    db_path = DEFAULT_DB

    # ------------------------------------------------------------ 工具

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status)

    def log_message(self, fmt, *args):  # 安静一点,只留错误
        if str(args[1] if len(args) > 1 else "")[:1] in ("4", "5"):
            super().log_message(fmt, *args)

    # ------------------------------------------------------------ 路由

    # ------------------------------------------------------------ 同源校验

    def _guard(self, change: bool) -> bool:
        """Host 白名单 + 写请求的客户端标记与 Origin 校验。返回 False 表示已回过错误。

        防两件事:

        * **DNS rebinding** —— 攻击者域名解析到 127.0.0.1,浏览器带着假 Host 来读工况数据。
          所以任何请求的 Host 都必须是本机回环。
        * **跨站请求** —— 任意网页都能向 127.0.0.1 发 `text/plain` 的「简单请求」,
          不触发预检;能删工况、换库、关服务。
          写请求要求带 `X-Casebook-Client: 1`:跨站页面加自定义头会触发预检,
          而本服务不回应预检,浏览器直接不发。

        代价:命令行脚本调写接口要自己带这个头(目前没有脚本这么做)。
        """
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        host = (self.headers.get("Host") or "").strip().lower()
        if host and host not in allowed:
            self._error("拒绝:Host 不在本机白名单(可能是 DNS rebinding)", 403)
            return False
        if not change:
            return True
        if self.headers.get("X-Casebook-Client") != "1":
            self._error("拒绝:写请求缺少 X-Casebook-Client 标记", 403)
            return False
        origin = (self.headers.get("Origin") or "").strip().lower()
        if origin and origin not in {f"http://{h}" for h in allowed}:
            self._error("拒绝:跨站来源", 403)
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if not self._guard(change=False):
                return
            if path.startswith("/api/"):
                self._api(path, query)
            else:
                self._static(path)
        except BrokenPipeError:
            pass
        except BadQuery as exc:  # 查询参数不合法:回 400,不要静默换默认值
            self._error(str(exc))
        except Exception as exc:  # 兜底,别让服务挂掉
            self._error(f"服务器内部错误:{type(exc).__name__}: {exc}", 500)

    def _read_json_body(self, max_bytes: int | None = None) -> dict:
        """读 POST 的 JSON body。

        没有 body(长度 0)仍按空对象处理 —— 有几个接口根本不需要 body。
        但"body 坏了"必须抛 BadBody 走 400,不能悄悄退回 `{}`:空对象在某些
        保存接口里表示"清空",一个损坏的请求就能把用户配置改掉。
        """
        max_bytes = self.JSON_BODY_LIMIT if max_bytes is None else max_bytes
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or 0)
        except (TypeError, ValueError):
            raise BadBody(f"Content-Length 不是整数:{raw_length!r}") from None
        if length <= 0:
            return {}
        if length > max_bytes:
            raise BadBody(f"请求体 {length} 字节超过上限 {max_bytes} 字节", 413)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise BadBody("请求体读到一半就断了")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise BadBody(f"请求体不是合法 JSON:{exc}") from None
        if not isinstance(body, dict):
            raise BadBody("请求体必须是 JSON 对象")
        return body

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            if not self._guard(change=True):
                return
            found, m = match(ROUTES, "POST", path)
            if found is None:
                self._error("未知接口", 404)
                return
            getattr(self, found.handler)(m, {})
        except BrokenPipeError:
            pass
        except BadBody as exc:
            self._error(str(exc), exc.status)
        except BadQuery as exc:
            self._error(str(exc))
        except Exception as exc:
            self._error(f"服务器内部错误:{type(exc).__name__}: {exc}", 500)

    def do_DELETE(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            if not self._guard(change=True):
                return
            found, m = match(ROUTES, "DELETE", path)
            if found is None:
                self._error("未知接口", 404)
                return
            getattr(self, found.handler)(m, {})
        except BrokenPipeError:
            pass
        except BadQuery as exc:
            self._error(str(exc))
        except Exception as exc:
            self._error(f"服务器内部错误:{type(exc).__name__}: {exc}", 500)

    def _api(self, path: str, query: dict | None = None) -> None:
        """GET 分发:先查不依赖工况的路由表,再进「按工况」的子路由。"""
        query = query or {}
        found, m = match(ROUTES, "GET", path)
        if found is not None:
            getattr(self, found.handler)(m, query)
            return
        if path == "/api/cases" or path.startswith("/api/cases/"):
            self._api_cases(path, query)
            return
        self._error("未知接口", 404)

    def _api_cases(self, path: str, query: dict) -> None:
        """按工况的 GET:解析 id → 查路由表 → 开库 → 确认工况存在 → 调用。

        这三件事以前在每个分支里重复一遍;现在只在这里做一次,handler 拿到的
        `case` 就是已查出来的那一行。注意先查表再开库:路径不认识时不必碰数据库。
        """
        parts = [p for p in path.split("/") if p]
        try:
            case_id = int(parts[2])
        except (IndexError, ValueError):
            self._error("工况 id 必须是整数")
            return
        found, m = match(CASE_ROUTES, "GET", path)
        if found is None:
            self._error("未知接口", 404)
            return
        conn = db.open_db(self.db_path)
        try:
            case = db.get_case(conn, case_id)
            if case is None:
                self._error(f"没有 id={case_id} 的工况", 404)
                return
            getattr(self, found.handler)(conn, case, m, query)
        finally:
            conn.close()

    # ------------------------------------------------------------ 标准表模板下载

    # ------------------------------------------------------------ 流股组成按需读

    # ------------------------------------------------------------ AI 分析包导出

    # ------------------------------------------------------------ Excel 导出

    # ------------------------------------------------------------ 衡算报告

    def _icon(self) -> None:
        """网页 favicon:复用桌面快捷方式那张 .ico(没有就回 204,页面照常能用)。"""
        if not os.path.isfile(ICON_PATH):
            self.send_response(204)
            self.end_headers()
            return
        with open(ICON_PATH, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", "image/x-icon")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> None:
        if path == "/favicon.ico":
            self._icon()
            return
        if path == "/apple-touch-icon.png":
            self.send_response(204)
            self.end_headers()
            return
        rel = path.lstrip("/") or "index.html"
        # 判边界要用 realpath + commonpath:单靠 startswith 时,兄弟目录
        # `../web-xxx/...` 经 normpath 之后仍以 WEB_DIR 开头,能绕过去。
        web_root = os.path.realpath(WEB_DIR)
        full = os.path.realpath(os.path.join(web_root, rel))
        try:
            inside = os.path.commonpath([web_root, full]) == web_root
        except ValueError:  # 跨盘符时 commonpath 直接抛,当越界处理
            inside = False
        if not inside:
            self._error("非法路径", 403)
            return
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            self._error("文件不存在", 404)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header(
            "Content-Type", f"{ctype}; charset=utf-8" if ctype.startswith("text") else ctype
        )
        self.send_header("Content-Length", str(len(body)))
        # 本地应用,更新后必须立刻生效:否则浏览器会拿旧 app.js 配新 index.html,
        # 表现为"新按钮点了没反应",非常难排查。
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _stop_after_window_closed(httpd) -> None:
    """用户点了窗口右上角的 ✕ —— 服务跟着退出,避免后台残留。"""
    _say("应用窗口已关闭 → 停止服务")
    threading.Thread(target=httpd.shutdown, daemon=True).start()


def build_server(
    db_path: str = DEFAULT_DB, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    """建一个 HTTP 服务但**不启动**:桌面壳要自己控制端口与生命周期。

    端口传 0 表示让系统挑一个空闲端口(桌面壳用这个,避免和浏览器模式的 8000 撞车)。
    绑定失败直接抛 OSError,由调用方决定怎么解释(见 `serve()` 的 10048 / 10013 分支)。
    """
    handler = type("BoundHandler", (Handler,), {"db_path": db_path})
    # 关掉端口复用:否则同时开两个服务会都"成功"绑上同一端口,请求随机落到旧进程
    ThreadingHTTPServer.allow_reuse_address = False
    return ThreadingHTTPServer((host, port), handler)


def serve(
    db_path: str = DEFAULT_DB,
    host: str = "127.0.0.1",
    port: int = 8000,
    open_browser: bool = True,
    app_window: bool = True,
) -> None:
    url = f"http://{host}:{port}"
    if host not in ("127.0.0.1", "localhost", "::1"):
        # 安装说明对外承诺「只监听本机」;允许改 host 就意味着把工况数据暴露到网络,
        # 而本服务没有登录、没有加密,必须明确警告而不是悄悄放行。
        warn = (
            f"⚠ 正在监听 {host},不是本机回环。\n"
            "  本服务没有登录与加密,局域网内任何人都能读取工况数据、删除工况。\n"
            "  只有在你清楚后果时才这样用。"
        )
        _say(warn)
    try:
        httpd = build_server(db_path, host, port)
    except OSError as exc:
        # 绑定失败有两种原因,处理方式完全不同,不能都当成"已经在运行":
        #   10048 = 端口确实被占(多半是服务已在跑)→ 聚焦已有窗口
        #   10013 = 系统拒绝监听(端口被排除/被安全软件拦)→ 必须弹窗说明,
        #           否则 pythonw 下双击"毫无反应",用户根本不知道发生了什么
        if getattr(exc, "winerror", None) == 10013:
            msg = (
                f"端口 {port} 无法监听(系统拒绝):{exc}\n\n"
                "常见原因:该端口落在 Windows 的排除端口段里(Hyper-V / WSL 会保留一些端口),\n"
                "或者被安全软件拦截。换个端口即可,例如:\n"
                f"    python -m app.api --port {port + 20}\n"
                "(桌面快捷方式的「目标」里也要跟着改。)"
            )
            _say(msg)
            _alert(msg)
            return
        # 已经在跑了:把已有窗口提到前台,不重复起第二个服务/第二个窗口
        _say(f"端口 {port} 已被占用 —— 服务应该已经在运行,打开 {url}")
        existing = win.find_window(WINDOW_KEY)
        if existing:
            win.focus_window(existing)
        elif open_browser:
            open_ui(url, app_window, win.load_state(STATE_PATH))
        return

    _write_pid_file(port)
    _say(f"Casebook 已启动:{url}")
    _say(f"工况库:{os.path.abspath(db_path)}")
    if open_browser:
        state = win.load_state(STATE_PATH)
        mode = open_ui(url, app_window, state)
        _say(f"已用 {mode} 方式打开界面" + (" (恢复上次窗口尺寸)" if state else ""))
        if mode == "app":
            # 记录窗口几何 + 窗口关闭时停服务
            win.start_watch(
                WINDOW_KEY, STATE_PATH, on_closed=lambda: _stop_after_window_closed(httpd), log=_say
            )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _say("\n已停止")
    finally:
        httpd.server_close()
        _remove_pid_file()


def _sqlite_side_files(db_path: Path) -> list[Path]:
    """SQLite 的附属文件(-wal / -shm / -journal);换库时要一并处理。"""
    return [Path(str(db_path) + suffix) for suffix in ("-wal", "-shm", "-journal")]


def _sqlite_copy(src: Path, dst: Path) -> None:
    """用 SQLite 的 backup API 复制数据库。

    直接 copy2 在库正被读写时可能抄到"半提交"状态:主库与 WAL 不是一个时刻
    的东西。backup API 给的是提交点一致的副本。
    """
    src_conn = sqlite3.connect("file:" + src.as_posix() + "?mode=ro", uri=True)
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()


def _sqlite_ok(db_path: Path) -> bool:
    """PRAGMA integrity_check 是不是 ok。"""
    conn = sqlite3.connect(str(db_path))
    try:
        verdict = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    return bool(verdict) and str(verdict[0]).lower() == "ok"


def _checkpoint_db(db_path: Path) -> None:
    """把 WAL 合回主库(库不存在或没有 WAL 时什么也不做)。"""
    if not db_path.is_file():
        return
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    except sqlite3.Error as exc:
        _say(f"WAL 合回主库失败(继续):{exc}")
    finally:
        conn.close()


def _write_pid_file(port: int) -> None:
    """记下 PID 与端口,给「停止 Casebook」用 —— 比按端口猜进程可靠。"""
    try:
        pid_file = Path(PID_PATH)
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(
            json.dumps(
                {"pid": os.getpid(), "port": port, "started": time.strftime("%Y-%m-%d %H:%M:%S")},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        _say(f"写 PID 文件失败(不影响服务):{exc}")


def _remove_pid_file() -> None:
    """退出时删掉自己的 PID 文件;不是自己写的就别动。"""
    try:
        pid_file = Path(PID_PATH)
        data = json.loads(pid_file.read_text(encoding="utf-8"))
        if int(data.get("pid") or 0) == os.getpid():
            pid_file.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        pass


def _install_sample(db_path: str, alert: bool = True) -> tuple[bool, str]:
    """把内置的示例工况库装成当前库(`--sample` 与首页按钮共用)。

    换库走四步:样例库先抄进临时文件 → 校验完整性 → 原库做一致性备份 →
    os.replace 原子替换。原库备份名带时间戳,不覆盖上一次的备份。
    返回 (是否成功, 给人看的说明)。
    """
    src = paths.resource_root() / "samples" / "case-23-error.db"
    if not src.is_file():
        msg = (
            f"这个版本里没有内置示例工况库(缺 {src})。"
            "可以到仓库的 samples/ 目录拿 case-23-error.db。"
        )
        _say(msg)
        if alert:
            _alert(msg)
        return False, msg
    dst = Path(db_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with _SAMPLE_SWAP_LOCK:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        staging = dst.with_name(f"{dst.name}.installing")
        for side in _sqlite_side_files(staging):
            side.unlink(missing_ok=True)
        backup_note = ""
        try:
            # ① 样例库先抄进临时文件:backup API 给的是提交点一致的副本
            _sqlite_copy(src, staging)
            # ② 临时库自己先过完整性检查,坏的那份不许取代真库
            if not _sqlite_ok(staging):
                staging.unlink(missing_ok=True)
                msg = "示例库完整性校验没通过,已放弃;原库未改动。"
                _say(msg)
                if alert:
                    _alert(msg)
                return False, msg
            # ③ 原库:同样用 backup API 做一致性备份,不覆盖上一次的
            if dst.is_file():
                backup = dst.with_name(f"{dst.name}.{stamp}.bak")
                _sqlite_copy(dst, backup)
                _say(f"已把原工况库备份成:{backup}")
                backup_note = f";原库已备份成 {backup.name}"
            # ④ 先把 WAL 合回主库再删附属文件 —— 顺序反了会丢已提交未落盘的数据
            _checkpoint_db(dst)
            for side in _sqlite_side_files(dst):
                side.unlink(missing_ok=True)
            # ⑤ 同盘 os.replace 是原子的:页面要么看到旧库,要么看到新库
            os.replace(staging, dst)
        except (OSError, sqlite3.Error) as exc:
            staging.unlink(missing_ok=True)
            msg = f"装示例库失败:{type(exc).__name__}: {exc}(原库 {dst.name} 未改动)"
            _say(msg)
            if alert:
                _alert(msg)
            return False, msg
    msg = f"已装入示例工况:121 流股 / 77 模块,含两个塔的报错{backup_note}"
    _say(msg)
    return True, msg


def main() -> int:
    ap = argparse.ArgumentParser(description="Casebook 本地服务")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"工况库路径(默认 {DEFAULT_DB})")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true", help="启动后不自动打开界面")
    ap.add_argument(
        "--app", action="store_true", help="用无地址栏的应用窗口打开(默认用普通浏览器标签页)"
    )
    ap.add_argument("--log", default=DEFAULT_LOG, help=f"无控制台时的日志文件(默认 {DEFAULT_LOG})")
    ap.add_argument(
        "--sample",
        action="store_true",
        help="把内置的示例工况库装进当前库(没有 Aspen 也能先看界面;原库会备份成 .bak)",
    )
    args = ap.parse_args()

    _ensure_logging(args.log)
    _say(f"启动 Casebook (pid={os.getpid()})")
    if args.sample:
        _install_sample(args.db)
    _say("提示:没有 Aspen 时,可以在首页点「载入示例工况」,或用 --sample 启动。")
    try:
        serve(args.db, args.host, args.port, open_browser=not args.no_browser, app_window=args.app)
    except Exception as exc:
        traceback.print_exc()
        _alert(f"Casebook 启动失败:\n{type(exc).__name__}: {exc}\n\n详情见 {args.log}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

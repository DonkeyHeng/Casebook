"""app/desktop.py —— 桌面壳:内置 WebView2 窗口,不需要本机浏览器。

为什么单独一个入口
------------------
`python -m app.api` 的界面是"Edge 的 --app 窗口"(见 app/api/window.py);这个入口
把窗口换成 pywebview(Windows 上就是系统自带的 WebView2 运行时)。页面、HTTP 接口、
数据库、Aspen 读取全部复用,一行不改 —— 换的只是"谁画这个窗口"。

三处必须自己处理,因为浏览器替我们做过:
  1. 端口:不再固定 8000,让系统挑空闲端口,和浏览器模式可以同时开着;
  2. 下载:WebView 里没有"下载栏",导出文件改由主进程写进 data/exports/ 并打开文件夹
     (`Api.save_export`);页面侧 `static/download.js` 检测到桥接才走这条路;
  3. 关窗即退:窗口关掉要停掉后台 HTTP 服务,否则进程会挂着。

跑法:
    python -m app.desktop                 # 开窗口
    python -m app.desktop --smoke         # 隐藏窗口自检:打印页面标题与桥接状态后退出
    python -m app.desktop --debug         # 打开 WebView 开发者工具

窗口起不来(极少见,通常是 Win10 太老没装 WebView2 运行时)时自动退回浏览器窗口,
和现在的 start.cmd 表现一致,不会变成"双击没反应"。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from app import paths

#: 延迟导入:app.api 一导入就会把 data/ 下的路径算成模块常量,
#: 所以必须等 --data-dir 设好再 import(见 run())
srv = None


def state_path() -> str:
    """窗口几何文件的位置;`--data-dir` 改过 data/ 之后要现算,不能在导入时定死。"""
    return str(paths.data_dir("window.json"))


# Windows 文件名里不能出现的字符;只取末段,防止页面传 "../" 之类
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_name(name: str) -> str:
    """把响应头里的文件名压成安全的单段名字(不改中文,Windows 支持)。"""
    base = _BAD_NAME.sub("_", Path(str(name or "")).name).strip(" .")
    return (base or "export")[:120]


def _unique(folder: Path, name: str) -> Path:
    """同名不覆盖:第二次导出同一份报告时存成 `xxx (2).docx`。"""
    path = folder / name
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    index = 2
    while True:
        candidate = folder / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


class Api:
    """暴露给页面的少量主进程能力:存导出文件、开外链。

    浏览器模式下这些方法不存在,前端 `download.js` 会自动退回原来的跳转下载。
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self._window = None

    def attach(self, window) -> None:
        """窗口建好后再挂上来:`quit()` 要销毁的就是它。"""
        self._window = window

    def quit(self) -> dict:
        """页面点「退出」→ 由主进程销毁窗口。

        WebView 里页面不是脚本打开的,`window.close()` 关不掉自己,只能退化成一张
        "已退出"白页;所以这件事交给主进程做。放到 Timer 里而不是当场调用:
        这里是 pywebview 的桥接线程,直接销毁窗口会和 GUI 线程互相等。
        """
        window = self._window
        if window is None:
            return {"ok": False, "error": "窗口还没就绪"}
        threading.Timer(0.2, window.destroy).start()
        return {"ok": True}

    def save_export(self, url: str) -> dict:
        """按页面给的相对地址取回文件 → 写进 data/exports/ → 打开所在文件夹。

        走本机 HTTP 取,而不是让页面把字节传过来:导出文件本来就是服务端生成的,
        少一次 base64 转码,大文件也不会卡住页面。
        """
        try:
            full = urllib.parse.urljoin(self.base_url + "/", str(url))
            with urllib.request.urlopen(full, timeout=300) as resp:  # noqa: S310 —— 只连本机
                blob = resp.read()
                name = self._name_from(resp.headers, full)
            folder = paths.data_dir("exports")
            folder.mkdir(parents=True, exist_ok=True)
            path = _unique(folder, name)
            path.write_bytes(blob)
            if sys.platform == "win32":
                os.startfile(str(folder))  # noqa: S606 —— 打开资源管理器,不执行内容
            return {"ok": True, "path": str(path), "bytes": len(blob)}
        except Exception as exc:  # 网络 / 磁盘 / 权限都可能,一律回给页面显示
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def open_external(self, url: str) -> dict:
        """外链(如中联选型)交给系统默认浏览器:WebView 里点开就困在窗口里了。"""
        import webbrowser

        target = str(url or "")
        if not target.lower().startswith(("http://", "https://")):
            return {"ok": False, "error": "只允许 http/https"}
        webbrowser.open(target)
        return {"ok": True}

    @staticmethod
    def _name_from(headers, full: str) -> str:
        """文件名优先取 Content-Disposition(服务端定的),没有再退回 URL 末段。"""
        disposition = headers.get("Content-Disposition", "") or ""
        match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disposition)
        if match:
            return _safe_name(urllib.parse.unquote(match.group(1)))
        return _safe_name(Path(urllib.parse.urlparse(full).path).name or "export")


def _fallback(base: str, httpd, smoke: bool = False) -> int:
    """WebView 起不来时退回浏览器窗口:行为与 start.cmd 一致,别让用户看到没反应。

    自检(`--smoke`)时不开窗口:browser 窗口要人手动关,自动化会一直挂着。
    """
    if smoke:
        print("自检失败:WebView 窗口没起来,且 --smoke 下不退回浏览器窗口")
        httpd.shutdown()
        httpd.server_close()
        return 2
    httpd.shutdown()  # 先停掉后台线程里的 serve_forever,否则下面不能再起一轮
    mode = srv.open_ui(base + "/", app_window=True, state=srv.win.load_state(state_path()))
    print(f"已退回浏览器窗口({mode}):{base}")
    srv.win.start_watch(srv.WINDOW_KEY, state_path(), on_closed=httpd.shutdown, log=print)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def _alert(msg: str) -> None:
    """桌面壳的错误弹窗:不复用 server 那个"仅无控制台才弹"的判断(这里永远无控制台)。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, msg, "Casebook", 0x10)
    except Exception:
        pass


def run(db_path: str, port: int = 0, debug: bool = False, smoke: bool = False) -> int:
    global srv
    if srv is None:
        from app.api import server as _server

        srv = _server
    try:
        import webview
    except Exception as exc:
        msg = f"桌面模式需要 pywebview,但导入失败:\n\n{exc}"
        print(msg)
        if not smoke:
            _alert(msg)
        return 1

    httpd = srv.build_server(db_path, "127.0.0.1", port)
    real_port = httpd.server_address[1]
    base = f"http://127.0.0.1:{real_port}"
    api = Api(base)
    threading.Thread(target=httpd.serve_forever, name="casebook-http", daemon=True).start()
    print(f"Casebook(桌面壳)已启动:{base}")
    print(f"工况库:{os.path.abspath(db_path)}")

    state = srv.win.load_state(state_path())
    window = webview.create_window(
        srv.WINDOW_KEY,
        base + "/",
        js_api=api,
        width=int(state.get("w") or 1280),
        height=int(state.get("h") or 820),
        x=state.get("x"),
        y=state.get("y"),
        min_size=(1000, 640),
        text_select=True,
        hidden=smoke,
        maximized=bool(state.get("maximized")),
    )
    api.attach(window)  # 桥接里的 quit() 靠它关窗

    def remember(*_args) -> None:
        """窗口尺寸/位置变了就记下来,下次原样恢复(浏览器模式下是 window.py 干这事)。"""
        try:
            width, height = int(window.width), int(window.height)
            if width > 200 and height > 100:
                srv.win.save_state(
                    state_path(),
                    {"x": window.x, "y": window.y, "w": width, "h": height, "maximized": False},
                )
        except Exception:
            pass

    if not smoke:
        window.events.resized += remember
        window.events.moved += remember

    def on_closed() -> None:
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    window.events.closed += on_closed

    if smoke:

        def probe() -> None:
            """隐藏窗口自检:等页面标题出来 → 看桥接方法在不在 → 关窗退出。"""
            deadline = time.time() + 25
            title = None
            while time.time() < deadline:
                try:
                    title = window.evaluate_js("document.title")
                    if title:
                        break
                except Exception:
                    pass
                time.sleep(0.5)
            bridged = False
            try:
                bridged = bool(
                    window.evaluate_js(
                        "!!(window.pywebview && window.pywebview.api"
                        " && window.pywebview.api.save_export"
                        " && window.pywebview.api.quit)"
                    )
                )
            except Exception:
                pass
            print(json.dumps({"title": title, "bridge": bridged}, ensure_ascii=False))
            try:
                window.destroy()
            except Exception:
                pass

        threading.Thread(target=probe, name="casebook-smoke", daemon=True).start()

    try:
        webview.start(
            debug=debug,
            private_mode=False,  # 留出 localStorage 的可能;私有模式每次启动都像新用户
            storage_path=str(paths.data_dir("webview")),
        )
    except Exception as exc:
        print(f"窗口起不来(多半是缺 WebView2 运行时):{exc}")
        return _fallback(base, httpd, smoke)
    finally:
        httpd.shutdown()
    httpd.server_close()
    if smoke:
        # 自检还要确认"关窗后服务真的退了",否则下次双击会多一个后台进程
        import socket

        closed = True
        try:
            with socket.create_connection(("127.0.0.1", real_port), timeout=2):
                closed = False
        except OSError:
            closed = True
        print(json.dumps({"port_closed": closed}, ensure_ascii=False))
    return 0


class _Tee:
    """把 print 同时写进日志文件。窗口程序没有控制台,不写文件就等于没有日志。

    有控制台(源码跑 / --console 打包的排查版)时照样往屏幕输出一份,方便直接看。
    """

    def __init__(self, path, echo=None) -> None:
        self.path = path
        self.echo = echo
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("a", encoding="utf-8", buffering=1)
        self.fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 =====\n")

    def write(self, text) -> None:
        self.fh.write(text)
        if self.echo is not None:
            try:
                self.echo.write(text)
            except Exception:
                pass

    def flush(self) -> None:
        try:
            self.fh.flush()
        except Exception:
            pass
        if self.echo is not None:
            try:
                self.echo.flush()
            except Exception:
                pass


def _setup_log() -> None:
    """桌面壳一律留日志(源码跑也是在 data/desktop.log):白屏/闪退时只能靠它。"""
    tee = _Tee(paths.data_dir("desktop.log"), echo=sys.stdout)
    sys.stdout = tee
    sys.stderr = tee


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Casebook 桌面壳(内置窗口,不依赖浏览器)")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="data/ 目录(默认在 exe 旁边);打包版指向仓库 data/ 就能复用标准表",
    )
    parser.add_argument("--db", default=None, help="工况库路径(默认 <data-dir>/cases.db)")
    parser.add_argument("--port", type=int, default=0, help="0 = 让系统挑一个空闲端口")
    parser.add_argument("--debug", action="store_true", help="打开 WebView 开发者工具")
    parser.add_argument("--smoke", action="store_true", help="隐藏窗口自检后退出")
    args = parser.parse_args(argv)

    if args.data_dir:
        paths.set_data_dir(args.data_dir)

    # 打包成 --windowed 后没有控制台,日志必须落文件,否则出错就是"窗口一闪没了"
    _setup_log()
    db_path = args.db or str(paths.data_dir("cases.db"))
    return run(db_path, args.port, args.debug, args.smoke)


if __name__ == "__main__":
    raise SystemExit(main())

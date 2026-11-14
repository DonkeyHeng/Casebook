# -*- coding: utf-8 -*-
"""界面回归自检:无头浏览器过一遍 6 个页签,截图 + 收集控制台报错。

改完 style.css / app.js 之后跑一次,比"肉眼看一眼"可靠:
它会把 JS 异常、console.error、以及请求失败都收集起来,退出码非 0 表示有问题。

    python -m tools.ui_sweep                     # 浅色 + 深色各扫一遍
    python -m tools.ui_sweep --theme light       # 只扫浅色
    python -m tools.ui_sweep --out data/preview/ui-check

依赖:websocket-client(`python -m pip install websocket-client`)、本机 Chrome 或 Edge。
服务要先起来(`python -m app.api --port 8000`);脚本不会自己启动服务。
截图落在 --out 指定目录,**不写进仓库**(那里默认在 .gitignore 里)。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

PAGES = ["home", "streams", "blocks", "compare", "flow", "pipes", "report"]

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str:
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("找不到 Chrome/Edge,请装一个,或用 --browser 指定路径")


class Cdp:
    """够用就好的 CDP 客户端:只做导航、执行脚本、截图。"""

    def __init__(self, port: int):
        import websocket  # 延迟导入:没装依赖时给出更清楚的报错

        self.ws = None
        deadline = time.time() + 20
        target = None
        while time.time() < deadline:
            try:
                raw = urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=2).read().decode()
                page = next(t for t in json.loads(raw) if t.get("type") == "page")
                target = page["webSocketDebuggerUrl"]
                break
            except Exception:
                time.sleep(0.25)
        if not target:
            raise SystemExit("浏览器没起来(调试端口无响应)")
        self.ws = websocket.create_connection(target, timeout=40, max_size=128 * 1024 * 1024)
        self._id = 0
        self.errors: list[str] = []
        self.cmd("Page.enable")
        self.cmd("Runtime.enable")
        self.cmd("Log.enable")

    def cmd(self, method: str, **params):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("method") == "Runtime.exceptionThrown":
                detail = msg["params"]["exceptionDetails"]
                exc = (detail.get("exception") or {}).get("description", "")
                self.errors.append(f"JS 异常: {exc[:200] or detail.get('text', '')}")
                continue
            if msg.get("method") == "Log.entryAdded":
                entry = msg["params"]["entry"]
                if entry.get("level") == "error":
                    self.errors.append(f"{entry.get('source', 'log')}: {entry.get('text', '')[:200]}")
                continue
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def js(self, expr: str):
        r = self.cmd("Runtime.evaluate", expression=expr,
                     returnByValue=True, awaitPromise=True)
        if r.get("exceptionDetails"):
            return None
        return r["result"].get("value")

    def goto(self, url: str, wait_ms: int = 2200) -> None:
        self.cmd("Page.navigate", url=url)
        time.sleep(wait_ms / 1000.0)
        for _ in range(40):
            if self.js("document.readyState") == "complete":
                return
            time.sleep(0.2)

    def shot(self, path: str) -> None:
        r = self.cmd("Page.captureScreenshot", format="png")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def run(base: str, out_dir: str, themes: list[str], browser: str, width: int, height: int) -> int:
    port = 9333
    profile = tempfile.mkdtemp(prefix="casebook-ui-sweep-")
    proc = subprocess.Popen(
        [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--no-first-run", "--no-default-browser-check",
         f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         f"--window-size={width},{height}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cdp = None
    try:
        cdp = Cdp(port)
        for theme in themes:
            # 先设好主题再进页面,避免截图时还在"上一套主题"里
            cdp.goto(base + "/#home", 1200)
            cdp.js(f"""(() => {{
              document.documentElement.dataset.theme = "{theme}";
              try {{ localStorage.setItem("casebook-theme", "{theme}"); }} catch (e) {{}}
              return 1;
            }})()""")
            for name in PAGES:
                cdp.goto(f"{base}/#{name}")
                cdp.shot(os.path.join(out_dir, f"{theme}-{name}.png"))
        return len(cdp.errors)
    finally:
        if cdp:
            for err in cdp.errors:
                print("  [报错]", err)
            cdp.close()
        proc.terminate()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Casebook 界面回归自检")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="服务地址")
    ap.add_argument("--out", default=os.path.join("data", "preview", "ui-check"),
                    help="截图输出目录(默认 data/preview/ui-check,不入库)")
    ap.add_argument("--theme", choices=["light", "dark", "both"], default="both")
    ap.add_argument("--browser", default=None, help="浏览器可执行文件路径")
    ap.add_argument("--size", default="1440x900", help="窗口尺寸,如 1024x768")
    args = ap.parse_args(argv)

    try:
        width, height = (int(v) for v in args.size.lower().split("x"))
    except ValueError:
        raise SystemExit("--size 要写成 1024x768 这种") from None

    themes = ["light", "dark"] if args.theme == "both" else [args.theme]
    browser = args.browser or find_browser()

    print(f"浏览器: {browser}")
    print(f"目标:   {args.base}  主题: {'+'.join(themes)}  尺寸: {width}x{height}")
    print(f"截图:   {args.out}")
    errors = run(args.base, args.out, themes, browser, width, height)
    if errors:
        print(f"发现 {errors} 条控制台报错,见上。")
        return 1
    print(f"{len(PAGES)} 个页签 × {len(themes)} 套主题,无控制台报错。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

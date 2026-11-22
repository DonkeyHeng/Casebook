"""HTTP 入口:请求体校验、静态路径、路由表。

用例正文原样搬自 `tools/selftest.py`(迁移到 pytest 时只改了位置)。
"""

from __future__ import annotations

import shutil

from tests._harness import (
    REPO_ROOT,
    check,
)


def test_http_entry() -> None:
    """POST body 坏掉必须回 400;静态路径不能穿越。

    以前 _read_json_body() 一律退回 `{}`,而空对象在等级表保存里表示"清空配置",
    一个坏请求就能把用户配的等级表删掉 —— 这几条是那次的回归网。
    用进程内随机端口起服务,不占固定端口、不留后台进程。
    """
    import json as jsonlib
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from app.api import server as srv
    from app.design.pipe import data as pipe_data

    root = REPO_ROOT
    tmp = root / "data" / "preview" / "_selftest-http"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    cfg = tmp / "piping-classes.json"
    # 改到临时目录:自测绝不碰用户真实的 data/config/piping-classes.json
    old_path, old_dir = pipe_data.PIPING_CLASS_PATH, pipe_data._CONFIG_DIR
    pipe_data.PIPING_CLASS_PATH, pipe_data._CONFIG_DIR = cfg, tmp

    handler_cls = type("SelftestHandler", (srv.Handler,), {"db_path": str(tmp / "none.db")})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def call(method: str, path: str, body=None, raw: bytes | None = None) -> int:
        headers = {"X-Casebook-Client": "1"}
        data = None
        if raw is not None:
            data, headers["Content-Type"] = raw, "application/json"
        elif body is not None:
            data = jsonlib.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except Exception:
            return 0

    try:
        check("HTTP:坏 JSON 回 400", call("POST", "/api/piping-classes", raw=b"{oops") == 400)
        check(
            "HTTP:空对象不再等于清空配置",
            call("POST", "/api/piping-classes", body={}) == 400 and not cfg.is_file(),
        )
        check("HTTP:数组 body 回 400", call("POST", "/api/piping-classes", raw=b"[1]") == 400)
        # 声明一个超过上限的 Content-Length 但不真发 body:服务在读 body 之前就回 413,
        # 客户端也能干净地读到这个状态码(真发 300 KB 会让服务端 RST,状态码读不到)
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=15)
        try:
            conn.putrequest("POST", "/api/piping-classes")
            conn.putheader("X-Casebook-Client", "1")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", "300000")
            conn.endheaders()
            status = conn.getresponse().status
        finally:
            conn.close()
        check("HTTP:超大 body 回 413", status == 413, f"status={status}")
        good = {"rules": [{"medium": ["PL"], "grade": "20#", "corrosion_mm": 1.0}], "default": None}
        check(
            "HTTP:合法等级表仍能保存",
            call("POST", "/api/piping-classes", body=good) == 200 and cfg.is_file(),
        )
        check(
            "HTTP:显式空表才清空",
            call("POST", "/api/piping-classes", body={"rules": [], "default": None}) == 200
            and not cfg.is_file(),
        )
        for probe in ("/../data/cases.db", "/..%2Fdata%2Fcases.db"):
            check(f"HTTP:静态路径穿越被拒 {probe}", call("GET", probe) == 403)
        check("HTTP:首页仍可读", call("GET", "/") == 200)

        # 两册《计算说明书》:管道页 / 泵页的「导出计算书」直接下发 docs 里的 Word 原件。
        # 只认"是个能打开的 docx + 中文名解得回来",不卡字节数 —— 说明书一改版就变。
        import urllib.parse

        for kind, title in (
            ("pipe", "Casebook-管路选型计算说明书.docx"),
            ("pump", "Casebook-泵计算说明书.docx"),
        ):
            req = urllib.request.Request(
                base + f"/api/manuals/{kind}.docx", headers={"X-Casebook-Client": "1"}
            )
            body: bytes = b""
            disposition = ""
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    body = resp.read()
                    disposition = resp.headers.get("Content-Disposition", "")
            except Exception:
                pass
            check(
                f"HTTP:计算书 {kind} 能下载",
                body[:2] == b"PK"
                and len(body) > 50_000
                and title
                == urllib.parse.unquote(disposition.split("filename*=UTF-8''")[-1].split(";")[0]),
                f"bytes={len(body)} cd={disposition[:40]}",
            )
        check("HTTP:没有的计算书回 404", call("GET", "/api/manuals/pump.txt") == 404)
    finally:
        httpd.shutdown()
        httpd.server_close()
        pipe_data.PIPING_CLASS_PATH, pipe_data._CONFIG_DIR = old_path, old_dir
        shutil.rmtree(tmp, ignore_errors=True)
        pipe_data.reload_piping_classes()


def test_routes() -> None:
    """路由表:每个处理函数都存在,且每条只读 GET 实跑不 500。

    这两个断言是给"按域拆 Handler"上的保险:表里写错一个方法名,或某个域的
    方法漏搬、少一个 import,都只会在用户点到那个页面时才炸。这里一次跑全。

    不碰写接口:POST 里有"载入示例库"(会换掉当前库)、DELETE 会删工况,
    这些只核对处理函数存在,不真发请求。
    """
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from app.api import routes as route_table
    from app.api import server as srv

    handlers = set(route_table.ROUTES) | set(route_table.CASE_ROUTES)
    missing = [item.handler for item in handlers if not hasattr(srv.Handler, item.handler)]
    check("路由表:处理函数都在 Handler 上", not missing, f"缺 {missing}")

    seen = set()
    dup = []
    for item in list(route_table.ROUTES) + list(route_table.CASE_ROUTES):
        key = (item.method, item.pattern.pattern)
        if key in seen:
            dup.append(key)
        seen.add(key)
    check("路由表:没有重复登记", not dup, f"重复 {dup}")

    # 只读 GET 全跑一遍:把 {case_id} 换成样本库里的工况,{块名}/{流股} 用真名字
    root = REPO_ROOT
    sample = root / "samples" / "case-23-error.db"
    if not sample.is_file():
        print("  找不到样本库,跳过实跑")
        return
    import sqlite3

    conn = sqlite3.connect(sample)
    try:
        case_id = conn.execute("SELECT id FROM cases ORDER BY id LIMIT 1").fetchone()[0]
        stream = conn.execute(
            "SELECT name FROM streams WHERE case_id = ? LIMIT 1", (case_id,)
        ).fetchone()
        block = conn.execute(
            "SELECT name FROM blocks WHERE case_id = ? LIMIT 1", (case_id,)
        ).fetchone()
    finally:
        conn.close()
    if stream is None or block is None:
        print("  样本库没有流股/设备,跳过实跑")
        return

    handler_cls = type("RouteProbeHandler", (srv.Handler,), {"db_path": str(sample)})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def get(path: str) -> int:
        req = urllib.request.Request(base + path, headers={"X-Casebook-Client": "1"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp.read()
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except Exception:
            return 0

    # 组成接口要连 Aspen,没有 Aspen 的机器上会回 4xx/5xx;它不在"只读不碰 Aspen"之列
    skip = {"/api/cases/(?P<case_id>\\d+)/streams/(?P<stream>[^/]+)/composition"}
    bad = []
    try:
        for item in route_table.CASE_ROUTES:
            if item.method != "GET" or item.pattern.pattern in skip:
                continue
            path = (
                item.pattern.pattern.replace("(?P<case_id>\\d+)", str(case_id))
                .replace("(?P<stream>[^/]+)", stream[0])
                .replace("(?P<block>[^/]+)", block[0])
                .replace("\\", "")
            )
            status = get(path)
            if status >= 500 or status == 0:
                bad.append(f"{path} → {status}")
    finally:
        httpd.shutdown()
        httpd.server_close()

    check("路由表:每条只读 GET 都能跑通(不 500)", not bad, ";".join(bad[:4]))


def test_guard() -> None:
    """_guard:Host 白名单 + 写请求客户端标记 + Origin。不启服务,直接喂假请求头。"""
    from app.api import server

    class FakeServer:
        server_address = ("127.0.0.1", 8000)

    class Fake:
        def __init__(self, headers):
            self.headers = headers
            self.server = FakeServer()
            self.err = None

        def _error(self, message, status=400):
            self.err = status

    guard = server.Handler._guard

    good = Fake({"Host": "127.0.0.1:8000", "X-Casebook-Client": "1"})
    check(
        "守卫:同源写请求放行",
        guard(good, change=True) is True and good.err is None,
        f"err={good.err}",
    )

    read = Fake({"Host": "localhost:8000"})
    check("守卫:GET 不需要客户端标记", guard(read, change=False) is True, f"err={read.err}")

    rebind = Fake({"Host": "evil.example", "X-Casebook-Client": "1"})
    check(
        "守卫:假 Host 拒绝(DNS rebinding)",
        guard(rebind, change=False) is False and rebind.err == 403,
        f"err={rebind.err}",
    )

    cross = Fake({"Host": "127.0.0.1:8000", "Content-Type": "text/plain"})
    check(
        "守卫:跨站简单请求拒绝",
        guard(cross, change=True) is False and cross.err == 403,
        f"err={cross.err}",
    )

    origin = Fake(
        {"Host": "127.0.0.1:8000", "X-Casebook-Client": "1", "Origin": "http://evil.example"}
    )
    check(
        "守卫:跨站 Origin 拒绝",
        guard(origin, change=True) is False and origin.err == 403,
        f"err={origin.err}",
    )

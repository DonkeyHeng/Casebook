"""本地 HTTP 服务:后端 API + 静态前端。

启动:
    python -m app.api                 # 默认 http://127.0.0.1:8000
    python -m app.api --port 8800 --db data/cases.db

接口:
    GET  /api/health                 健康检查
    POST /api/read                   从已打开的 Aspen 读一次(后台线程,立刻返回)
    GET  /api/read/status            读取进度/结果(前端每 0.5 s 问一次)
    GET  /api/cases                  工况列表
    GET  /api/cases/{id}             单个工况详情(含收敛状态与原因)
    GET  /api/cases/{id}/streams     流股表
    GET  /api/cases/{id}/streams/{流股}/composition   单条流股的组成(按需从 Aspen 读)
    GET  /api/cases/{id}/blocks      设备表
    GET  /api/cases/{id}/export.xlsx      导出 Excel(给人看)
    GET  /api/cases/{id}/export.ai.zip    导出 AI 分析包(Markdown + CSV,给 AI 读)

设计:
  * 只用标准库,不引第三方依赖(打包 exe 时省事)
  * 读 Aspen 的动作走 `app.api.read_job` 的后台线程:服务由 pythonw 启动、没有控制台,
    进度只能靠 `/api/read/status` 轮询回页面
  * 只读:除了"读工况"和"写这次的快照",不碰用户的模型
  * 每个请求单独开库,避免多线程共享连接
"""
from __future__ import annotations

import argparse
import io
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
from urllib.parse import parse_qs, unquote, urlparse

from app.store import db
from app.api import read_job
from app.api import window as win

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
PROJECT_ROOT = os.path.dirname(os.path.dirname(WEB_DIR))
DEFAULT_DB = "data/cases.db"
DEFAULT_LOG = "data/server.log"
STATE_PATH = os.path.join(PROJECT_ROOT, "data", "window.json")

# 页面标题(窗口标题也是它),用来定位应用窗口;改 <title> 时要同步改这里
WINDOW_KEY = "aspen-insight · 工况数据"

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
    fh = open(log_path, "a", encoding="utf-8", buffering=1)
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

        ctypes.windll.user32.MessageBoxW(0, msg, "aspen-insight", 0x10)
    except Exception:
        pass


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


class Handler(BaseHTTPRequestHandler):
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

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                self._api(path, query)
            else:
                self._static(path)
        except BrokenPipeError:
            pass
        except Exception as exc:  # 兜底,别让服务挂掉
            self._error(f"服务器内部错误:{type(exc).__name__}: {exc}", 500)

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/shutdown":
                self._json({"ok": True, "message": "服务已停止"})
                # 必须换个线程关,否则会在请求处理线程里自己等自己
                threading.Timer(0.2, self.server.shutdown).start()
                return
            if path == "/api/read":
                self._start_read()
                return
            self._error("未知接口", 404)
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._error(f"服务器内部错误:{type(exc).__name__}: {exc}", 500)

    def _start_read(self) -> None:
        """POST /api/read —— 起一个后台读取任务,立刻返回当前状态。

        不让请求线程同步等 9–25 s:Aspen 的 COM 调用卡住时页面会一直转圈,
        用户看不出是"在算"还是"死了"。轮询能显示进度,也能显示卡在哪一步。
        """
        snap, started = read_job.start(self.db_path)
        if not started:
            self._json({
                "ok": False,
                "error": "已经有一次读取在进行中,等它结束再点。",
                "job": snap,
            }, 409)
            return
        self._json({"ok": True, "job": snap})

    def _api(self, path: str, query: dict | None = None) -> None:
        parts = [p for p in path.split("/") if p]  # ['api', 'cases', '1', 'streams']
        query = query or {}
        if parts == ["api", "health"]:
            self._json({"ok": True, "db": self.db_path})
            return
        if parts == ["api", "read", "status"]:
            self._json(read_job.snapshot())
            return
        if parts == ["api", "compare"]:
            self._compare(query)
            return
        if parts[:2] != ["api", "cases"]:
            self._error("未知接口", 404)
            return

        conn = db.open_db(self.db_path)
        try:
            if len(parts) == 2:                       # /api/cases
                self._json([_row_to_dict(r) for r in db.list_cases(conn)])
                return

            try:
                case_id = int(parts[2])
            except ValueError:
                self._error("工况 id 必须是整数")
                return

            case = db.get_case(conn, case_id)
            if case is None:
                self._error(f"没有 id={case_id} 的工况", 404)
                return

            if len(parts) == 3:                       # /api/cases/{id}
                payload = _row_to_dict(case)
                for key in ("reasons_json", "status_json", "tear_json",
                            "block_errors", "block_warnings"):
                    if payload.get(key):
                        try:
                            payload[key.replace("_json", "")] = json.loads(payload[key])
                        except (TypeError, ValueError):
                            pass
                self._json(payload)
                return

            if parts[3] == "streams":
                # /api/cases/{id}/streams/{流股}/composition —— 单条流股的组成(按需)
                if len(parts) >= 6 and parts[5] == "composition":
                    self._stream_composition(conn, case_id, parts[4])
                    return
                self._json([_row_to_dict(r) for r in db.read_streams(conn, case_id)])
                return
            if parts[3] == "blocks":
                self._json([_row_to_dict(r) for r in db.read_blocks(conn, case_id)])
                return
            if parts[3] == "columns":
                if len(parts) == 4:
                    self._json([_row_to_dict(r) for r in db.list_columns(conn, case_id)])
                else:
                    block = unquote(parts[4])
                    self._json([_row_to_dict(r)
                                for r in db.read_column_stages(conn, case_id, block)])
                return
            if parts[3] == "export.xlsx":
                self._export(conn, case_id)
                return
            if parts[3] == "export.ai.zip":
                self._export_ai(conn, case_id)
                return
            self._error("未知接口", 404)
        finally:
            conn.close()

    # ------------------------------------------------------------ 流股组成按需读

    def _stream_composition(self, conn, case_id: int, stream: str) -> None:
        """GET /api/cases/{id}/streams/{流股}/composition

        先看工况库里存过没有;没存过就去当前打开的 Aspen 现读一条(约 0.3 s)并缓存。
        组成不跟着主读取一起读 —— 全量读会让 121 条流股从 9 s 变 41 s。
        """
        from app.api import composition

        case = db.get_case(conn, case_id)
        if case is None:
            self._error(f"没有 id={case_id} 的工况", 404)
            return
        try:
            payload = composition.get(conn, case, stream)
        except composition.CompositionError as exc:
            # 409:不是接口有问题,是"现在这个状态给不了",原因原样显示给用户
            self._json({"error": str(exc), "stream": stream}, 409)
            return
        self._json(payload)

    # ------------------------------------------------------------ AI 分析包导出

    def _export_ai(self, conn, case_id: int) -> None:
        """把工况导成给 AI 读的一组文件(Markdown + CSV + JSON,打成 zip)。"""
        from app.api import aipack

        try:
            data, fname = aipack.build_zip(conn, case_id)
        except LookupError:
            self._error(f"没有 id={case_id} 的工况", 404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------ Excel 导出

    def _export(self, conn, case_id: int) -> None:
        """把工况导成一个 Excel 工作簿(概要 / 流股 / 设备 / 塔剖面 四张表)。"""
        try:
            import openpyxl
            from openpyxl.styles import Alignment, Font
            from openpyxl.utils import get_column_letter
        except ImportError:
            self._error("服务器没有安装 openpyxl,无法导出 Excel。请先 pip install openpyxl", 500)
            return

        case = db.get_case(conn, case_id)
        if case is None:
            self._error(f"没有 id={case_id} 的工况", 404)
            return

        streams = db.read_streams(conn, case_id)
        blocks = db.read_blocks(conn, case_id)
        columns = db.list_columns(conn, case_id)

        status_text = {0: "正常", 1: "错误", 2: "告警"}

        wb = openpyxl.Workbook()

        def fill_sheet(ws, headers, rows, widths=None):
            ws.append(headers)
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal="center")
            for row in rows:
                ws.append(list(row))
            ws.freeze_panes = "A2"
            if widths:
                for i, w in enumerate(widths, 1):
                    ws.column_dimensions[get_column_letter(i)].width = w

        # --- 概要 ---
        ws = wb.active
        ws.title = "概要"
        reasons = json.loads(case["reasons_json"] or "[]")
        summary = [
            ("模型", case["model_path"]),
            ("读取时间", case["read_at"]),
            ("运行 ID", case["run_id"]),
            ("运行时间", case["run_date"]),
            ("Aspen 版本", case["aspen_version"]),
            ("收敛判定", {"ok": "收敛", "warning": "有告警", "error": "有错误"}
             .get(case["severity"], case["severity"])),
            ("是否可用于统计对比", "是" if case["converged"] else "否"),
            ("流股数", case["stream_count"]),
            ("设备数", case["block_count"]),
            ("结论", "\n".join(f"- {r}" for r in reasons) or "无异常"),
        ]
        for key, value in summary:
            ws.append([key, value])
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=1):
            row[0].font = Font(bold=True)
        ws.column_dimensions["A"].width = 20
        ws.column_dimensions["B"].width = 90
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=2, max_col=2):
            row[0].alignment = Alignment(wrap_text=True, vertical="top")

        # --- 流股 ---
        ws = wb.create_sheet("流股")
        fill_sheet(
            ws,
            ["流股", "温度 ℃", "压力 bar", "质量流量 kg/h", "摩尔流量 kmol/h", "汽化率", "体积流量 m³/h"],
            [(s["name"], s["temperature_c"], s["pressure_bar"], s["mass_flow_kgh"],
              s["mole_flow_kmolh"], s["vap_frac"], s["vol_flow_m3h"]) for s in streams],
            [18, 12, 12, 16, 18, 10, 16],
        )

        # --- 设备 ---
        ws = wb.create_sheet("设备")
        fill_sheet(
            ws,
            ["设备", "类型", "状态", "热负荷 kW", "功率 kW", "换热面积 m²",
             "理论级数", "顶温 ℃", "底温 ℃", "提示"],
            [(b["name"], b["type"], status_text.get(b["status"], ""), b["duty_kw"],
              b["power_kw"], b["area_m2"], b["nstage"], b["top_temp_c"],
              b["bottom_temp_c"], b["message"]) for b in blocks],
            [14, 12, 8, 14, 12, 14, 10, 12, 12, 60],
        )

        # --- 塔剖面 ---
        ws = wb.create_sheet("塔剖面")
        stage_rows = []
        for col in columns:
            for st in db.read_column_stages(conn, case_id, col["block"]):
                stage_rows.append((col["block"], st["stage"], st["temperature_c"],
                                   st["pressure_bar"], st["liq_flow"], st["vap_flow"],
                                   st["duty_kw"]))
        fill_sheet(
            ws,
            ["塔", "级", "温度 ℃", "压力 bar", "液流量 kmol/h", "气流量 kmol/h", "热负荷 kW"],
            stage_rows,
            [12, 8, 12, 12, 16, 16, 14],
        )

        bio = io.BytesIO()
        wb.save(bio)
        data = bio.getvalue()

        stamp = time.strftime("%Y%m%d_%H%M")
        fname = f"aspen-insight_case{case_id}_{stamp}.xlsx"
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _compare(self, query: dict) -> None:
        """GET /api/compare?base=1&other=2 —— 逐流股对比两个工况。"""
        try:
            base_id = int(query["base"][0])
            other_id = int(query["other"][0])
        except (KeyError, ValueError, IndexError):
            self._error("需要 base 和 other 两个工况 id,例如 /api/compare?base=1&other=2")
            return

        fields = ["temperature_c", "pressure_bar", "mass_flow_kgh",
                  "mole_flow_kmolh", "vap_frac", "vol_flow_m3h"]

        conn = db.open_db(self.db_path)
        try:
            base = db.get_case(conn, base_id)
            other = db.get_case(conn, other_id)
            if base is None or other is None:
                self._error("工况不存在", 404)
                return

            left = {r["name"]: _row_to_dict(r) for r in db.read_streams(conn, base_id)}
            right = {r["name"]: _row_to_dict(r) for r in db.read_streams(conn, other_id)}

            rows, common, changed = [], 0, 0
            for name in sorted(set(left) | set(right)):
                a, b = left.get(name), right.get(name)
                delta = {}
                for f in fields:
                    va = a.get(f) if a else None
                    vb = b.get(f) if b else None
                    delta[f] = round(vb - va, 6) if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else None
                if a and b:
                    common += 1
                    if any(d not in (None, 0) for d in delta.values()):
                        changed += 1
                rows.append({
                    "name": name,
                    "base": {f: a.get(f) for f in fields} if a else None,
                    "other": {f: b.get(f) for f in fields} if b else None,
                    "delta": delta,
                })

            self._json({
                "base": {"id": base["id"], "model_name": base["model_name"],
                         "read_at": base["read_at"], "severity": base["severity"]},
                "other": {"id": other["id"], "model_name": other["model_name"],
                          "read_at": other["read_at"], "severity": other["severity"]},
                "fields": fields,
                "streams": rows,
                "summary": {
                    "total": len(rows),
                    "common": common,
                    "changed": changed,
                    "only_base": sorted(set(left) - set(right)),
                    "only_other": sorted(set(right) - set(left)),
                },
            })
        finally:
            conn.close()

    def _static(self, path: str) -> None:
        if path in ("/favicon.ico", "/apple-touch-icon.png"):
            self.send_response(204)
            self.end_headers()
            return
        rel = path.lstrip("/") or "index.html"
        if rel.startswith("static/"):
            rel = rel
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.normpath(WEB_DIR)):
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
        self.send_header("Content-Type", f"{ctype}; charset=utf-8" if ctype.startswith("text") else ctype)
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


def serve(db_path: str = DEFAULT_DB, host: str = "127.0.0.1", port: int = 8000,
          open_browser: bool = True, app_window: bool = True) -> None:
    url = f"http://{host}:{port}"
    handler = type("BoundHandler", (Handler,), {"db_path": db_path})
    # 关掉端口复用:否则同时开两个服务会都"成功"绑上同一端口,请求随机落到旧进程
    ThreadingHTTPServer.allow_reuse_address = False
    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError:
        # 已经在跑了:把已有窗口提到前台,不重复起第二个服务/第二个窗口
        _say(f"端口 {port} 已被占用 —— 服务应该已经在运行,打开 {url}")
        existing = win.find_window(WINDOW_KEY)
        if existing:
            win.focus_window(existing)
        elif open_browser:
            open_ui(url, app_window, win.load_state(STATE_PATH))
        return

    _say(f"aspen-insight 已启动:{url}")
    _say(f"工况库:{os.path.abspath(db_path)}")
    if open_browser:
        state = win.load_state(STATE_PATH)
        mode = open_ui(url, app_window, state)
        _say(f"已用 {mode} 方式打开界面" + (" (恢复上次窗口尺寸)" if state else ""))
        if mode == "app":
            # 记录窗口几何 + 窗口关闭时停服务
            win.start_watch(WINDOW_KEY, STATE_PATH,
                            on_closed=lambda: _stop_after_window_closed(httpd),
                            log=_say)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _say("\n已停止")
    finally:
        httpd.server_close()


def main() -> int:
    ap = argparse.ArgumentParser(description="aspen-insight 本地服务")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"工况库路径(默认 {DEFAULT_DB})")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true", help="启动后不自动打开界面")
    ap.add_argument("--tab", action="store_true", help="用普通浏览器标签页打开(默认应用窗口)")
    ap.add_argument("--log", default=DEFAULT_LOG, help=f"无控制台时的日志文件(默认 {DEFAULT_LOG})")
    args = ap.parse_args()

    _ensure_logging(args.log)
    _say(f"启动 aspen-insight (pid={os.getpid()})")
    try:
        serve(args.db, args.host, args.port,
              open_browser=not args.no_browser,
              app_window=not args.tab)
    except Exception as exc:
        traceback.print_exc()
        _alert(f"aspen-insight 启动失败:\n{type(exc).__name__}: {exc}\n\n详情见 {args.log}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

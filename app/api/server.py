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
    GET  /api/cases/{id}/connections 设备 ↔ 流股连接关系(拓扑:进出流股 / 换热器分侧 / 塔进料板)
    GET  /api/cases/{id}/flowsheet   流程图数据(节点 = 设备、边 = 设备间流股、边界流股)
    GET  /api/cases/{id}/impact      报错设备的波及范围(哪些流股的数据不能用)
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
import shutil
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
from app.store import db, flowsheet, impact
from app.api import read_job
from app.api import window as win

# 只读资源(网页、图标)与可写数据(data/)分开:打包成 exe 后前者在临时解包目录,
# 后者必须在 exe 旁边,否则一关程序数据就没了(见 app/paths.py)
WEB_DIR = os.path.join(str(paths.resource_root()), "app", "web")
PROJECT_ROOT = str(paths.resource_root())
DEFAULT_DB = str(paths.data_dir("cases.db"))
DEFAULT_LOG = str(paths.data_dir("server.log"))
STATE_PATH = str(paths.data_dir("window.json"))

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

        ctypes.windll.user32.MessageBoxW(0, msg, "Casebook", 0x10)
    except Exception:
        pass


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def _pipe_table_status() -> dict:
    """管路选型依赖的三张标准表录到什么程度(不连 Aspen、不查库)。"""
    from app.design import pipe_data

    stress_grades = sorted(pipe_data.ALLOWABLE_STRESS)
    return {
        "dn_to_od": {"loaded": bool(pipe_data.DN_TO_OD), "count": len(pipe_data.DN_TO_OD),
                     "verified": sorted(pipe_data.DN_OD_VERIFIED)},
        "od_to_walls": {"loaded": bool(pipe_data.OD_TO_WALLS),
                        "count": len(pipe_data.OD_TO_WALLS)},
        "stress": {"loaded": bool(pipe_data.ALLOWABLE_STRESS),
                   "grades": stress_grades,
                   "count": sum(len(v) for v in pipe_data.ALLOWABLE_STRESS.values())},
        # 管道等级表:不填也能跑 —— 全部流股按页面上的默认材质
        "piping_class": pipe_data.piping_class_status(),
        "ready": bool(pipe_data.DN_TO_OD and pipe_data.ALLOWABLE_STRESS
                      and pipe_data.OD_TO_WALLS),
    }


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

    def _read_json_body(self) -> dict:
        """读 POST 的 JSON body;读不到就当空对象(参数校验会兜住)。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return {}
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {}
        return body if isinstance(body, dict) else {}

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
            if path == "/api/piping-classes":
                # 保存管道等级表:校验 → 写 data/config/piping-classes.json → 重新加载
                from app.design import pipe_data

                ok, errors = pipe_data.save_piping_classes(self._read_json_body())
                payload = {"ok": ok, "errors": errors,
                           "status": pipe_data.piping_class_status()}
                self._json(payload, 200 if ok else 400)
                return
            if path == "/api/pipe-standards":
                # 导入标准表:校验 → 写 data/preview/std/docx/ → 立即重新加载(不用重启)
                from app.design import pipe_data

                ok, errors, files = pipe_data.import_standards(self._read_json_body())
                payload = {"ok": ok, "errors": errors,
                           "dir": files["dir"], "files": files["files"],
                           "status": _pipe_table_status()}
                if ok:
                    payload["loaded"] = {
                        "od_to_walls": len(pipe_data.OD_TO_WALLS),
                        "od_to_walls_sst": len(pipe_data.OD_TO_WALLS_SST),
                        "stress_grades": sorted(pipe_data.ALLOWABLE_STRESS),
                    }
                self._json(payload, 200 if ok else 400)
                return
            if path == "/api/sample/install":
                # 首页「载入示例工况」按钮:把内置示例库装进当前库(先备份原库)
                ok, message = _install_sample(self.db_path, alert=False)
                payload = {"ok": ok, "message": message}
                if ok:
                    conn = db.open_db(self.db_path)
                    try:
                        cases = db.list_cases(conn)
                    finally:
                        conn.close()
                    payload["case_id"] = cases[0]["id"] if cases else None
                self._json(payload, 200 if ok else 500)
                return
            self._error("未知接口", 404)
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._error(f"服务器内部错误:{type(exc).__name__}: {exc}", 500)

    def do_DELETE(self) -> None:  # noqa: N802
        """DELETE /api/cases/{id} —— 删掉一个工况及其明细(页面上的「删除工况」)。"""
        path = unquote(urlparse(self.path).path)
        parts = [p for p in path.split("/") if p]
        try:
            if len(parts) == 3 and parts[:2] == ["api", "cases"]:
                self._delete_case(parts[2])
                return
            if parts == ["api", "piping-classes"]:
                # 清除等级表 = 回到"全部按默认材质"
                from app.design import pipe_data

                ok, errors = pipe_data.save_piping_classes({"rules": [], "default": None})
                self._json({"ok": ok, "errors": errors,
                            "status": pipe_data.piping_class_status()}, 200 if ok else 400)
                return
            self._error("未知接口", 404)
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._error(f"服务器内部错误:{type(exc).__name__}: {exc}", 500)

    def _delete_case(self, raw_id: str) -> None:
        try:
            case_id = int(raw_id)
        except ValueError:
            self._error("工况 id 必须是整数")
            return

        conn = db.open_db(self.db_path)
        try:
            case = db.get_case(conn, case_id)
            if case is None:
                self._error(f"没有 id={case_id} 的工况", 404)
                return
            removed = db.delete_case(conn, case_id)
            self._json({
                "ok": True,
                "deleted": case_id,
                "model_name": case["model_name"],
                "read_at": case["read_at"],
                "removed": removed,
            })
        finally:
            conn.close()

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
        if parts == ["api", "pipe-tables"]:
            # 管路选型要用的标准表录到什么程度(前端空状态用)
            self._json(_pipe_table_status())
            return
        if parts == ["api", "pipe-standards"]:
            # 标准表导入弹窗:三个 CSV 各自有没有、多少行、什么时候放进来的
            from app.design import pipe_data

            files = pipe_data.standards_status()
            self._json({"dir": files["dir"], "files": files["files"],
                        "status": _pipe_table_status()})
            return
        if parts == ["api", "piping-classes"]:
            # 管道等级表的当前内容(页面上的「等级表」弹窗读它)
            from app.design import pipe_data

            self._json({
                "status": pipe_data.piping_class_status(),
                "rules": pipe_data.PIPING_CLASS_RULES,
                "default": pipe_data.PIPING_CLASS_DEFAULT,
                "grades": list(pipe_data.PIPING_CLASS_GRADES),
                "media": list(pipe_data.PIPING_CLASS_MEDIA),
                "file": "data/config/piping-classes.json",
            })
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
                # 首页仪表盘要用的两项:连接条数、焓平衡四态台数
                payload["connection_count"] = len(db.read_connections(conn, case_id))
                payload["balance_summary"] = db.balance_summary(conn, case_id)
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
            if parts[3] == "connections":
                self._json([_row_to_dict(r) for r in db.read_connections(conn, case_id)])
                return
            if parts[3] == "flowsheet":
                payload = flowsheet.build(
                    [dict(r) for r in db.read_connections(conn, case_id)],
                    [dict(r) for r in db.read_blocks(conn, case_id)],
                )
                self._json(payload)
                return
            if parts[3] == "impact":
                # 报错设备的进出口流股 —— 直接从 connections 反查,不用再连 Aspen
                self._json(impact.build(
                    [dict(r) for r in db.read_connections(conn, case_id)],
                    [dict(r) for r in db.read_blocks(conn, case_id)],
                ))
                return
            if parts[3] == "pipes":
                self._pipes(conn, case_id, query)
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

    def _pipes(self, conn, case_id: int, query: dict) -> None:
        """GET /api/cases/{id}/pipes —— 管路选型(实时算,不写库)。

        参数(都可省):`liquid` / `gas` / `two`(m/s)、`factor`(设计压力系数)、
        `floor`(设计压力下限 MPa)、`max_dn`、`material`(默认牌号)。
        标准表没录全时返回 409 并说明缺哪张,而不是吐一张空表。
        """
        from app.design import pipe as pipe_calc
        from app.design import pipe_data

        # query 的值是 list(parse_qs 的行为),取第一个;不取的话 float(list) 会失败,
        # 参数就被静默换成默认值了(界面改了流速、结果却不变,就是这样来的)
        def _q(key: str):
            val = query.get(key)
            if isinstance(val, (list, tuple)):
                return val[0] if val else None
            return val

        def _f(key: str, default: float) -> float:
            try:
                return float(_q(key))
            except (TypeError, ValueError):
                return default

        material = _q("material") or "S30408"
        if material not in pipe_data.MATERIAL_CODE_BY_GRADE:
            material = "S30408"      # 认不出的牌号不硬套,退回默认并在页面提示
        options = {
            "velocity": {
                "liquid": _f("liquid", pipe_calc.DEFAULT_VELOCITY["liquid"]),
                "gas": _f("gas", pipe_calc.DEFAULT_VELOCITY["gas"]),
                "two": _f("two", pipe_calc.DEFAULT_VELOCITY["two"]),
                "pump_inlet": _f("pump_inlet", pipe_calc.DEFAULT_VELOCITY["pump_inlet"]),
            },
            "pressure_factor": _f("factor", 1.1),
            "pressure_floor_mpa": _f("floor", 1.0),
            "max_dn": int(_f("max_dn", 600)),
            "material": material,
        }

        streams = [dict(r) for r in db.read_streams(conn, case_id)]
        blocks = [dict(r) for r in db.read_blocks(conn, case_id)]
        conns = [dict(r) for r in db.read_connections(conn, case_id)]

        try:
            result = pipe_calc.build(streams, blocks, conns, options=options)
        except pipe_calc.TablesNotReady as exc:
            # 表没录不是错误,是"还没装数据"——把要做什么一并给出来,别只丢一句报错
            payload = {"error": str(exc), "tables": _pipe_table_status(),
                       "setup": pipe_data.setup_guide()}
            self._json(payload, 409)
            return

        result["validation"] = pipe_calc.validate(result["rows"], options)
        result["options"] = options
        result["tables"] = _pipe_table_status()
        self._json(result)

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
        connections = db.read_connections(conn, case_id)

        status_text = {0: "正常", 1: "错误", 2: "告警"}
        balance_text = {"ok": "通过", "bad": "超差", "no_data": "无法校验", "skip": "不校验"}

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
            ("连接关系数", len(connections)),
            ("焓平衡 通过/超差/无法校验/不校验", "/".join(
                str(sum(1 for b in blocks if b["balance_state"] == st))
                for st in ("ok", "bad", "no_data", "skip"))),
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
            ["流股", "温度 ℃", "压力 bar", "质量流量 kg/h", "摩尔流量 kmol/h", "汽化率",
             "体积流量 m³/h", "质量焓 kJ/kg", "摩尔焓 kJ/kmol", "能量流 kW"],
            [(s["name"], s["temperature_c"], s["pressure_bar"], s["mass_flow_kgh"],
              s["mole_flow_kmolh"], s["vap_frac"], s["vol_flow_m3h"],
              s["enthalpy_kjkg"], s["enthalpy_kjkmol"], s["enthalpy_flow_kw"]) for s in streams],
            [18, 12, 12, 16, 18, 10, 16, 16, 18, 16],
        )

        # --- 设备 ---
        ws = wb.create_sheet("设备")
        fill_sheet(
            ws,
            ["设备", "类型", "状态", "热负荷 kW", "功率 kW", "换热面积 m²",
             "理论级数", "顶温 ℃", "底温 ℃", "ΔH kW", "参照 kW", "焓平衡", "平衡说明", "提示"],
            [(b["name"], b["type"], status_text.get(b["status"], ""), b["duty_kw"],
              b["power_kw"], b["area_m2"], b["nstage"], b["top_temp_c"],
              b["bottom_temp_c"], b["dh_kw"], b["ref_kw"],
              balance_text.get(b["balance_state"], ""), b["balance_note"],
              b["message"]) for b in blocks],
            [14, 12, 8, 14, 12, 14, 10, 12, 12, 14, 14, 10, 46, 60],
        )

        # --- 连接关系 ---
        ws = wb.create_sheet("连接")
        fill_sheet(
            ws,
            ["设备", "方向", "流股", "分侧", "端口", "进料板", "来源"],
            [(c["block"], "进" if c["direction"] == "in" else "出", c["stream"],
              c["side"] or "", c["port"] or "", c["stage"], c["source"] or "")
             for c in connections],
            [14, 8, 18, 10, 10, 10, 14],
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

        # --- 管道(管路选型:实时算,不写库)---
        # 标准表没录全时不硬造表,只写一张说明页
        try:
            from app.design import pipe as pipe_calc

            pres = pipe_calc.build([dict(s) for s in streams], [dict(b) for b in blocks],
                                   [dict(c) for c in connections])
            pchecks = pipe_calc.validate(pres["rows"])
            hit_by_stream: dict[str, list[str]] = {}
            for f in pchecks["findings"]:
                hit_by_stream.setdefault(f["stream"], []).append(f["code"])

            phase_text = {"liquid": "液体", "gas": "气体", "two": "气液共存"}
            ws = wb.create_sheet("管道")
            fill_sheet(
                ws,
                ["流股", "温度 ℃", "压力 MPa(绝)", "状态", "体积流量 m³/h", "公称直径",
                 "壁厚 mm", "管材", "类型", "等级号", "校验", "备注"],
                [(
                    r["stream"],
                    next((s["temperature_c"] for s in streams if s["name"] == r["stream"]), None),
                    next((s["pressure_bar"] / 10.0 for s in streams
                          if s["name"] == r["stream"]), None),
                    phase_text.get(r["phase"], r["phase"]),
                    r["vol_flow_m3h"], r["dn"], r["wall_mm"], r["material"], "无缝",
                    r["grade_code"], " ".join(hit_by_stream.get(r["stream"], [])),
                    "; ".join(r["warnings"]),
                ) for r in pres["rows"]],
                [16, 12, 16, 12, 18, 12, 10, 12, 8, 12, 12, 52],
            )
            if pres["missing"]:
                ws.append([])
                ws.append(["未选型的流股(缺数据或超出 DN600)"])
                for m in pres["missing"]:
                    ws.append([m["stream"], "", "", "", "", "", "", "", "", "", "", m["reason"]])

            ws = wb.create_sheet("管道计算书")
            fill_sheet(
                ws,
                ["流股", "相态", "介质代号", "流速 m/s", "计算内径 mm", "外径 mm",
                 "设计压力 MPa", "表压 MPa", "设计温度 ℃", "许用应力 MPa", "计算壁厚 mm",
                 "腐蚀裕量 mm", "所需壁厚 mm", "最小壁厚 mm", "选定壁厚 mm", "负偏差 mm",
                 "壁厚是否按下限", "等级号"],
                [(
                    r["stream"], r["phase"], r["medium_code"], r["velocity_ms"],
                    r["inner_d_mm"], r["od_mm"], r["design_pressure_mpa"],
                    r["gauge_pressure_mpa"], r["design_temp_c"], r["stress_mpa"],
                    r["t_calc_mm"], r["c2_mm"], r["t_required_mm"],
                    r.get("min_wall_mm"), r["wall_mm"],
                    (pipe_calc.pipe_data.negative_deviation(r["od_mm"], r["wall_mm"])
                     if r["wall_mm"] else None),
                    "是" if r.get("wall_floored") else "",
                    r["grade_code"],
                ) for r in pres["rows"]],
                [16, 10, 12, 12, 14, 12, 14, 12, 14, 14, 14, 14, 14, 14, 14, 12, 14, 12],
            )
        except pipe_calc.TablesNotReady as exc:   # 标准表没录:写明原因与补录办法,不造数
            ws = wb.create_sheet("管道")
            ws.append(["管路选型暂时不可用"])
            ws.append([str(exc)])
            guide = pipe_calc.pipe_data.setup_guide()
            ws.append([])
            ws.append(["为什么不在安装包里:", guide["why"]])
            ws.append(["放进哪个目录:", guide["dir"]])
            ws.append([])
            ws.append(["要准备的文件"])
            for item in guide["files"]:
                ws.append([item["name"], item["from"], item["columns"]])
            ws.append([])
            ws.append(["步骤"])
            for i, step in enumerate(guide["steps"], 1):
                ws.append([i, step])
            ws.append([])
            ws.append(["详细页码与分页结构:", guide["doc"]])
        except Exception as exc:                  # 选型出问题不该拖垮整份 Excel
            ws = wb.create_sheet("管道")
            ws.append(["管路选型出错"])
            ws.append([f"{type(exc).__name__}: {exc}"])

        bio = io.BytesIO()
        wb.save(bio)
        data = bio.getvalue()

        stamp = time.strftime("%Y%m%d_%H%M")
        fname = f"Casebook_case{case_id}_{stamp}.xlsx"
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
        """GET /api/compare?base=1&other=2 —— 逐流股 / 逐设备对比两个工况。"""
        try:
            base_id = int(query["base"][0])
            other_id = int(query["other"][0])
        except (KeyError, ValueError, IndexError):
            self._error("需要 base 和 other 两个工况 id,例如 /api/compare?base=1&other=2")
            return

        fields = ["temperature_c", "pressure_bar", "mass_flow_kgh",
                  "mole_flow_kmolh", "vap_frac", "vol_flow_m3h"]
        # 设备要比的字段:负荷/功率/级数/顶底温 + ΔH(焓平衡自检);状态与焓平衡按"变没变"处理
        block_fields = ["duty_kw", "power_kw", "nstage", "top_temp_c", "bottom_temp_c",
                        "dh_kw", "ref_kw"]

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

            # ---- 设备 ----
            b_left = {r["name"]: _row_to_dict(r) for r in db.read_blocks(conn, base_id)}
            b_right = {r["name"]: _row_to_dict(r) for r in db.read_blocks(conn, other_id)}
            block_rows, b_common, b_changed = [], 0, 0
            for name in sorted(set(b_left) | set(b_right)):
                a, b = b_left.get(name), b_right.get(name)
                delta = {}
                for f in block_fields:
                    va = a.get(f) if a else None
                    vb = b.get(f) if b else None
                    delta[f] = round(vb - va, 6) if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else None
                status_a = a.get("status") if a else None
                status_b = b.get("status") if b else None
                balance_a = a.get("balance_state") if a else None
                balance_b = b.get("balance_state") if b else None
                if a and b:
                    b_common += 1
                    if (any(d not in (None, 0) for d in delta.values())
                            or status_a != status_b
                            or balance_a != balance_b):
                        b_changed += 1
                block_rows.append({
                    "name": name,
                    "type": (a or b or {}).get("type"),
                    "base": {f: a.get(f) for f in block_fields} if a else None,
                    "other": {f: b.get(f) for f in block_fields} if b else None,
                    "delta": delta,
                    "status": {"base": status_a, "other": status_b},
                    # 焓平衡判定不能相减,只能并排看:A 通过 → B 超差 才算"变了"
                    "balance": {"base": balance_a, "other": balance_b},
                })

            self._json({
                "base": {"id": base["id"], "model_name": base["model_name"],
                         "read_at": base["read_at"], "severity": base["severity"]},
                "other": {"id": other["id"], "model_name": other["model_name"],
                          "read_at": other["read_at"], "severity": other["severity"]},
                "fields": fields,
                "block_fields": block_fields,
                "streams": rows,
                "blocks": block_rows,
                "summary": {
                    "total": len(rows),
                    "common": common,
                    "changed": changed,
                    "only_base": sorted(set(left) - set(right)),
                    "only_other": sorted(set(right) - set(left)),
                    "block_total": len(block_rows),
                    "block_common": b_common,
                    "block_changed": b_changed,
                    "block_only_base": sorted(set(b_left) - set(b_right)),
                    "block_only_other": sorted(set(b_right) - set(b_left)),
                },
            })
        finally:
            conn.close()

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
    except OSError as exc:
        # 绑定失败有两种原因,处理方式完全不同,不能都当成"已经在运行":
        #   10048 = 端口确实被占(多半是服务已在跑)→ 聚焦已有窗口
        #   10013 = 系统拒绝监听(端口被排除/被安全软件拦)→ 必须弹窗说明,
        #           否则 pythonw 下双击"毫无反应",用户根本不知道发生了什么
        if getattr(exc, "winerror", None) == 10013:
            msg = (f"端口 {port} 无法监听(系统拒绝):{exc}\n\n"
                   "常见原因:该端口落在 Windows 的排除端口段里(Hyper-V / WSL 会保留一些端口),\n"
                   "或者被安全软件拦截。换个端口即可,例如:\n"
                   f"    python -m app.api --port {port + 20}\n"
                   "(桌面快捷方式的「目标」里也要跟着改。)")
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

    _say(f"Casebook 已启动:{url}")
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


def _install_sample(db_path: str, alert: bool = True) -> tuple[bool, str]:
    """把内置的示例工况库装成当前库(`--sample` 与首页按钮共用)。

    会先把已有的库备份成 `*.bak`,不覆盖掉用户自己的数据。
    返回 (是否成功, 给人看的说明)。
    """
    src = paths.resource_root() / "samples" / "case-23-error.db"
    if not src.is_file():
        msg = (f"这个版本里没有内置示例工况库(缺 {src})。"
               "可以到仓库的 samples/ 目录拿 case-23-error.db。")
        _say(msg)
        if alert:
            _alert(msg)
        return False, msg
    dst = Path(db_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    backup_note = ""
    if dst.is_file():
        backup = dst.with_name(dst.name + ".bak")
        shutil.copy2(dst, backup)
        _say(f"已把原工况库备份成:{backup}")
        backup_note = f";原库已备份成 {backup.name}"
    # 清掉可能残留的 SQLite 附属文件,避免旧数据页混进新库
    for suffix in ("-wal", "-shm", "-journal"):
        side = Path(str(dst) + suffix)
        if side.is_file():
            side.unlink(missing_ok=True)
    shutil.copy2(src, dst)
    msg = f"已装入示例工况:121 流股 / 77 模块,含两个塔的报错{backup_note}"
    _say(msg)
    return True, msg


def main() -> int:
    ap = argparse.ArgumentParser(description="Casebook 本地服务")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"工况库路径(默认 {DEFAULT_DB})")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true", help="启动后不自动打开界面")
    ap.add_argument("--app", action="store_true", help="用无地址栏的应用窗口打开(默认用普通浏览器标签页)")
    ap.add_argument("--log", default=DEFAULT_LOG, help=f"无控制台时的日志文件(默认 {DEFAULT_LOG})")
    ap.add_argument("--sample", action="store_true",
                    help="把内置的示例工况库装进当前库(没有 Aspen 也能先看界面;原库会备份成 .bak)")
    args = ap.parse_args()

    _ensure_logging(args.log)
    _say(f"启动 Casebook (pid={os.getpid()})")
    if args.sample:
        _install_sample(args.db)
    _say("提示:没有 Aspen 时,可以在首页点「载入示例工况」,或用 --sample 启动。")
    try:
        serve(args.db, args.host, args.port,
              open_browser=not args.no_browser,
              app_window=args.app)
    except Exception as exc:
        traceback.print_exc()
        _alert(f"Casebook 启动失败:\n{type(exc).__name__}: {exc}\n\n详情见 {args.log}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""导出域:Excel / AI 分析包 / 管道一览表 / 衡算报告。

渲染都在 `app/export/`;这里只做"取数 → 渲染 → 回字节"和文件名。
"""

from __future__ import annotations

import json
import time

from app.api.serialize import row_to_dict
from app.export import aipack, family_xlsx, pipe_schedule, report
from app.export.workbook import build_workbook
from app.store import db


class ExportRoutes:
    """导出域的路由与处理。"""

    def _export_ai(self, conn, case_id: int) -> None:
        """把工况导成给 AI 读的一组文件(Markdown + CSV + JSON,打成 zip)。"""
        from app.export import aipack

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

    def _export(self, conn, case_id: int) -> None:
        """把工况导成一个 Excel 工作簿;构建逻辑在模块函数 build_workbook()。"""
        try:
            data = build_workbook(conn, case_id)
        except ImportError:
            self._error("服务器没有安装 openpyxl,无法导出 Excel。请先 pip install openpyxl", 500)
            return
        except LookupError:
            self._error(f"没有 id={case_id} 的工况", 404)
            return

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

    def _pipes_docx(self, conn, case_id: int) -> None:
        """GET /api/cases/{id}/pipes.docx —— 管道选型一览表(单张 Word)。

        不并进 Excel 工作簿:那 9 列是版面表(列宽/字体/跨页表头),Word 才装得下;
        工作簿继续管它的流股 / 设备 / 计算书。
        """
        from app.export import pipe_schedule

        try:
            data, fname = pipe_schedule.build_pipe_docx(conn, case_id)
        except ImportError:
            self._error("服务器没有安装 python-docx,无法导出 Word。请先 pip install python-docx", 500)
            return
        except LookupError:
            self._error(f"没有 id={case_id} 的工况", 404)
            return
        except Exception as exc:  # 标准表没录、流股缺数等:把原因原样说清,不造数
            self._error(f"管道表生成失败:{exc}", 400)
            return

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _export_family(self, conn, case_id: int, query: dict) -> None:
        """GET /api/cases/{id}/export-family.xlsx?family=pump —— 单分类设备表。

        设备页右上角「导出 Excel」用它:只导当前分类,泵分类就是选型要抄的那几列。
        """
        from app.export import family_xlsx

        raw = query.get("family")
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        family = str(raw or "").strip()
        if not family:
            self._error("需要 family 参数,例如 ?family=pump", 400)
            return
        try:
            data, _label, fname = family_xlsx.build_family_workbook(conn, case_id, family)
        except ValueError:
            self._error(f"未知分类:{family}", 400)
            return
        except LookupError:
            self._error(f"没有 id={case_id} 的工况", 404)
            return
        except ImportError:
            self._error("服务器没有安装 openpyxl,无法导出 Excel。请先 pip install openpyxl", 500)
            return

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

    def _export_report(self, conn, case_id: int, query: dict) -> None:
        """GET /api/cases/{id}/balance-report.docx?blocks=&families= —— 衡算报告。

        `blocks` / `families` 取并集;都不给就是全部设备。工段边界自动读
        `data/config/sections.json`,没有确认过就用自动草稿(报告里会注明)。
        """
        from app.design import block_family
        from app.design import sections as section_rules
        from app.export import report
        from app.store import sections_config

        def _q(key: str) -> str:
            val = query.get(key)
            if isinstance(val, (list, tuple)):
                val = val[0] if val else ""
            return str(val or "").strip()

        blocks = [dict(r) for r in db.read_blocks(conn, case_id)]
        connections = [dict(r) for r in db.read_connections(conn, case_id)]

        picked: set[str] = set()
        picked.update(n for n in _q("blocks").replace(" ", "").split(",") if n)
        for key in _q("families").replace(" ", "").split(","):
            if key:
                picked.update(
                    b["name"]
            for b in blocks
            if block_family.family_of_block(b) == key
                )
        keys = {b["name"] for b in blocks}
        picked_list = sorted(picked & keys) or None

        confirmed, _overrides, deleted, _error = sections_config.load(case_id)
        parts = None
        if confirmed or deleted:
            parts = section_rules.merge(
                section_rules.draft(blocks, connections), confirmed, deleted
            )

        kind = _q("kind") or "all"
        if kind not in report.KINDS:
            self._error(f"未知册别:{kind}(可用 {'/'.join(report.KIND_KEYS)})", 400)
            return
        try:
            data, fname = report.build_report(conn, case_id, parts, picked_list, kind)
        except ImportError:
            self._error(
                "服务器没有安装 python-docx,无法生成 Word 报告。"
                '请先 pip install "python-docx>=1.1"',
                500,
            )
            return
        except LookupError:
            self._error(f"没有 id={case_id} 的工况", 404)
            return

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def get_export_xlsx(self, conn, case, m, query):
        """GET /api/cases/{id}/export.xlsx —— 整本 Excel(给人看)。"""
        self._export(conn, int(m["case_id"]))

    def get_export_family(self, conn, case, m, query):
        """GET /api/cases/{id}/export-family.xlsx —— 当前设备分类的 Excel。"""
        self._export_family(conn, int(m["case_id"]), query)

    def get_export_pipes_docx(self, conn, case, m, query):
        """GET /api/cases/{id}/pipes.docx —— 管道选型一览表 Word。"""
        self._pipes_docx(conn, int(m["case_id"]))

    def get_export_ai(self, conn, case, m, query):
        """GET /api/cases/{id}/export.ai.zip —— AI 分析包。"""
        self._export_ai(conn, int(m["case_id"]))

    def get_export_report(self, conn, case, m, query):
        """GET /api/cases/{id}/balance-report.docx —— 物料与能量衡算报告。"""
        self._export_report(conn, int(m["case_id"]), query)

"""换热器域:选型表、设计输入、配置读写(GET/POST)。

只做"从库里取数 → 调 `app.design.heat` 纯函数 → 回写人工决定",
不在这里拼公式,也不在这里读 Aspen。溯源标签由计算层给出,原样下发。
"""

from __future__ import annotations

import time

from app.design.heat import assemble
from app.design.heat import data as hx_data
from app.store import db, heat_config

HEAT_TYPES = assemble.HEAT_TYPES


class HeatRoutes:
    """换热器路由。

    GET 都走 CASE_ROUTES(拿 `conn` 与 `case`),写接口走 ROUTES(自己开库)。
    """

    # ------------------------------------------------------------ 读

    def get_heat_table(self, conn, case, m, query) -> None:
        """GET /api/cases/{id}/heat —— 换热设备 L1 一览(实时算,不落库)。"""
        case_id = case["id"]
        model_key = db.model_key(conn, case_id) or ""
        blocks = [dict(b) for b in db.read_blocks(conn, case_id)]
        conns = [dict(c) for c in db.read_connections(conn, case_id)]
        streams = [dict(s) for s in db.read_streams(conn, case_id)]
        heat_blocks = [b for b in blocks if (b.get("type") or "") in HEAT_TYPES]

        fouling_cfg, fouling_src, fouling_warn = heat_config.load_config(
            "fouling", hx_data.FOULING_DEFAULTS
        )
        u_cfg, u_src, u_warn = heat_config.load_config("u-reference", hx_data.U_REFERENCE_DEFAULTS)
        preset_cfg, preset_src, preset_warn = heat_config.load_config(
            "utility-presets", hx_data.UTILITY_PRESET_DEFAULTS
        )

        designs = {row["block"]: dict(row) for row in db.read_hx_design(conn, model_key)}
        selections = {row["block"]: dict(row) for row in db.read_hx_selection(conn, model_key)}

        configs = {
            "fouling": fouling_cfg,
            "u-reference": u_cfg,
            "utility-presets": preset_cfg,
        }
        rows = assemble.assemble(heat_blocks, conns, streams, designs, selections, configs)

        self._json(
            {
                "case_id": case_id,
                "model_key": model_key,
                "rows": rows,
                "config": {
                    "fouling": {"source": fouling_src, "warnings": fouling_warn},
                    "u-reference": {"source": u_src, "warnings": u_warn},
                    "utility-presets": {"source": preset_src, "warnings": preset_warn},
                },
                "boundary": "管壳式(BEM 为主,兼容固定管板/浮头/U 形管编码);板式/空冷器不在本期",
            }
        )

    # ------------------------------------------------------------ 写

    def post_heat_design(self, m, query) -> None:
        """POST /api/cases/{id}/heat/{block}/design —— 保存人工决定。

        body 里只接受 `hx_design` 的已知列;`tube_fluid` 按设备类型校验枚举。
        """
        try:
            case_id = int(m.group("case_id"))
        except (ValueError, IndexError):
            self._error("工况 id 必须是整数")
            return
        block = m.group("block")
        body = self._read_json_body()
        values = body.get("values") if isinstance(body.get("values"), dict) else body
        if not isinstance(values, dict):
            self._error("请求体必须是对象")
            return

        conn = db.open_db(self.db_path)
        try:
            blocks = [dict(b) for b in db.read_blocks(conn, case_id)]
            target = next((b for b in blocks if b.get("name") == block), None)
            if target is None:
                self._error(f"没有设备 {block}(工况 {case_id})", 404)
                return
            if (target.get("type") or "") not in HEAT_TYPES:
                self._error(f"{block} 不是换热类设备,不能写换热器设计")
                return

            if "tube_fluid" in values:
                problem = hx_data.check_tube_fluid(target.get("type"), values["tube_fluid"])
                if problem:
                    self._error(problem)
                    return
                values.setdefault("tube_fluid_source", "user")

            for key in ("fouling_tube", "fouling_shell", "margin", "f_correction"):
                if (
                    key in values
                    and values[key] is not None
                    and not isinstance(values[key], (int, float))
                ):
                    self._error(f"{key} 必须是数值或 null")
                    return

            model_key = db.model_key(conn, case_id)
            if not model_key:
                self._error("工况缺模型身份,无法按模型保存设计")
                return
            ok = db.save_hx_design(conn, model_key, block, values)
            if not ok:
                self._error("没有可写入的字段")
                return
        finally:
            conn.close()

        self._json({"ok": True, "block": block})

    def post_heat_selection(self, m, query) -> None:
        """POST /api/cases/{id}/heat/{block}/selection —— 保存选型台账。"""
        try:
            case_id = int(m.group("case_id"))
        except (ValueError, IndexError):
            self._error("工况 id 必须是整数")
            return
        block = m.group("block")
        body = self._read_json_body()
        values = body.get("values") if isinstance(body.get("values"), dict) else body
        if not isinstance(values, dict):
            self._error("请求体必须是对象")
            return

        conn = db.open_db(self.db_path)
        try:
            model_key = db.model_key(conn, case_id)
            if not model_key:
                self._error("工况缺模型身份,无法保存选型台账")
                return
            ok = db.save_hx_selection(conn, model_key, block, values)
            if not ok:
                self._error("没有可写入的字段")
                return
        finally:
            conn.close()

        self._json({"ok": True, "block": block})

    # ------------------------------------------------------------ 配置(不依赖工况)

    def get_heat_config(self, m, query) -> None:
        """GET /api/heat-config —— 三份配置的当前内容与来源。"""
        payload = {}
        for name, defaults in (
            ("fouling", hx_data.FOULING_DEFAULTS),
            ("u-reference", hx_data.U_REFERENCE_DEFAULTS),
            ("utility-presets", hx_data.UTILITY_PRESET_DEFAULTS),
        ):
            cfg, source, warnings = heat_config.load_config(name, defaults)
            payload[name] = {"data": cfg, "source": source, "warnings": warnings}
        payload["files"] = heat_config.CONFIG_FILES
        payload["labels"] = hx_data.SOURCE_LABELS
        self._json(payload)

    def post_heat_config(self, m, query) -> None:
        """POST /api/heat-config —— 保存一份用户配置(整体覆盖)。"""
        body = self._read_json_body()
        name = body.get("name")
        data = body.get("data")
        if name not in heat_config.CONFIG_FILES:
            self._error(f"未知配置名 {name!r};可选 {', '.join(heat_config.CONFIG_FILES)}")
            return
        if not isinstance(data, dict):
            self._error("data 必须是对象")
            return
        ok, message = heat_config.save_config(name, data)
        if not ok:
            self._error(message)
            return
        self._json({"ok": True, "message": message})

    # ------------------------------------------------------------ 导出

    def get_export_heat_edr_xlsx(self, conn, case, m, query) -> None:
        """GET /api/cases/{id}/heat-edr.xlsx —— EDR 输入参数清单。"""
        from app.export import heat as heat_export

        try:
            data = heat_export.build_edr_checklist(conn, case["id"])
        except LookupError as exc:
            self._error(str(exc), 404)
            return
        except ImportError:
            self._error("服务器没有安装 openpyxl,无法导出 Excel", 500)
            return

        stamp = time.strftime("%Y%m%d_%H%M")
        fname = f"Casebook_case{case['id']}_heat-EDR_{stamp}.xlsx"
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

    def delete_heat_config(self, m, query) -> None:
        """DELETE /api/heat-config?name=fouling —— 删除用户覆盖,恢复内置默认。"""
        raw = query.get("name")
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        name = str(raw or "").strip()
        ok, message = heat_config.reset_config(name)
        if not ok:
            self._error(message)
            return
        self._json({"ok": True, "message": message})

    def get_export_heat_docx(self, conn, case, m, query):
        """GET /api/cases/{id}/heat.docx —— 换热器选型一览表(§9.2 十五列)。"""
        from app.export import hx_schedule

        case_id = int(m["case_id"])
        try:
            data, filename = hx_schedule.build_hx_docx(conn, case_id)
        except LookupError as exc:
            self._error(str(exc), 404)
            return
        except ImportError:
            self._error(
                "服务器没有安装 python-docx,无法导出 Word。请先 pip install python-docx",
                500,
            )
            return

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

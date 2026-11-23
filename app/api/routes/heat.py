"""换热器域:选型表、设计输入、配置读写(GET/POST)。

只做"从库里取数 → 调 `app.design.heat` 纯函数 → 回写人工决定",
不在这里拼公式,也不在这里读 Aspen。溯源标签由计算层给出,原样下发。
"""

from __future__ import annotations

import io
import time

from app.design.heat import assemble, standards
from app.design.heat import data as hx_data
from app.design.heat import prompt as hx_prompt
from app.store import db, heat_config

HEAT_TYPES = assemble.HEAT_TYPES


def _one(value):
    """query 的值可能是 list(同名参数出现多次);取第一个,空则 None。"""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


class HeatRoutes:
    """换热器路由。

    GET 都走 CASE_ROUTES(拿 `conn` 与 `case`),写接口走 ROUTES(自己开库)。
    """

    # ------------------------------------------------------------ 读

    def get_heat_table(self, conn, case, m, query) -> None:
        """GET /api/cases/{id}/heat —— 换热设备 L1 一览(实时算,不落库)。"""
        case_id = case["id"]
        model_key = db.model_key(conn, case_id) or ""
        ctx = self._load_ctx(conn, case_id)
        heat_blocks = [b for b in ctx["blocks"] if (b.get("type") or "") in HEAT_TYPES]
        rows = assemble.assemble(
            heat_blocks,
            ctx["conns"],
            ctx["streams"],
            ctx["designs"],
            ctx["selections"],
            ctx["configs"],
        )

        self._json(
            {
                "case_id": case_id,
                "model_key": model_key,
                "rows": rows,
                "config": ctx["config_meta"],
                "std_state": standards.standard_state(),
                "boundary": "管壳式(BEM 为主,兼容固定管板/浮头/U 形管编码);板式/空冷器不在本期",
            }
        )

    def get_heat_detail(self, conn, case, m, query) -> None:
        """GET /api/cases/{id}/heat/{block} —— 单台详情。

        物性(cp / λ / μ)在**这里**按需读:先看 `hx_props` 缓存,不全才去 Aspen
        读 4 条侧流股并写缓存。读不到不报错,降级成 `props.source=unavailable`。
        """
        from app.api import hx_props

        block = m.group("block")
        case_id = case["id"]
        forced = str(_one(query.get("refresh")) or "").lower() in ("1", "true", "yes")
        ctx = self._load_ctx(conn, case_id)
        target = next((b for b in ctx["blocks"] if b.get("name") == block), None)
        if target is None:
            self._error(f"没有设备 {block}(工况 {case_id})", 404)
            return
        if (target.get("type") or "") not in HEAT_TYPES:
            self._error(f"{block} 不是换热类设备", 400)
            return

        row = assemble.build_row(
            target,
            ctx["conns"],
            ctx["streams"],
            ctx["designs"].get(block, {}),
            ctx["selections"].get(block, {}),
            ctx["configs"],
        )
        try:
            props = hx_props.get(conn, case, block, force=forced)
        except LookupError as exc:
            self._error(str(exc), 404)
            return

        # L2:标准行枚举 → 五态判定(纯函数;物性缺项不影响 L1)
        from app.design.heat import rating

        l2 = rating.evaluate(
            row["case"],
            props.get("sides"),
            stored=ctx["designs"].get(block) or {},
            snap=standards.snapshot(),
        )
        manual = l2.pop("manual", None)

        self._json(
            {
                "case_id": case_id,
                "block": block,
                "row": row,
                "props": props,
                "l2": l2,
                "manual": manual,
                "config": ctx["config_meta"],
                "std_state": standards.standard_state(),
            }
        )

    def _load_ctx(self, conn, case_id: int) -> dict:
        """把列表/详情共用的"工况数据 + 三份配置 + 已存决定"取一遍。"""
        blocks = [dict(b) for b in db.read_blocks(conn, case_id)]
        conns = [dict(c) for c in db.read_connections(conn, case_id)]
        streams = [dict(s) for s in db.read_streams(conn, case_id)]
        model_key = db.model_key(conn, case_id) or ""

        configs, meta = {}, {}
        for name, defaults in (
            ("fouling", hx_data.FOULING_DEFAULTS),
            ("u-reference", hx_data.U_REFERENCE_DEFAULTS),
            ("utility-presets", hx_data.UTILITY_PRESET_DEFAULTS),
        ):
            cfg, src, warnings = heat_config.load_config(name, defaults)
            configs[name] = cfg
            meta[name] = {"source": src, "warnings": warnings}

        return {
            "blocks": blocks,
            "conns": conns,
            "streams": streams,
            "model_key": model_key,
            "configs": configs,
            "config_meta": meta,
            "designs": {row["block"]: dict(row) for row in db.read_hx_design(conn, model_key)},
            "selections": {
                row["block"]: dict(row) for row in db.read_hx_selection(conn, model_key)
            },
        }

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

    # ------------------------------------------------------------ 标准系列表(不依赖工况)

    def get_hx_standards(self, m, query) -> None:
        """GET /api/hx-standards —— 两份 CSV 的状态 + 当前生效的版本与行数。"""
        self._json(standards.standards_status())

    def post_hx_standards(self, m, query) -> None:
        """POST /api/hx-standards —— 导入两份 CSV:校验 → 拼新版本 → 原子切指针。"""
        ok, errors, files = standards.import_standards(
            self._read_json_body(self.STANDARDS_BODY_LIMIT)
        )
        payload = {
            "ok": ok,
            "errors": errors,
            "warnings": files.get("warnings", []),
            "dir": files.get("dir"),
            "files": files.get("files", []),
            "state": standards.standard_state(),
        }
        if ok:
            snap = standards.snapshot()
            payload["loaded"] = {
                "rows": len(snap.rows),
                "standards": list(snap.standards),
                "version": snap.version,
                "passes": [p.as_dict() for p in snap.passes],
            }
        self._json(payload, 200 if ok else 400)

    def get_hx_standards_prompt(self, m, query) -> None:
        """GET /api/hx-standards-prompt —— 「复制填写提示词」正文。"""
        text = hx_prompt.standards_prompt()
        self._json({"version": hx_prompt.PROMPT_VERSION, "chars": len(text), "text": text})

    def get_hx_edr_prompt(self, m, query) -> None:
        """GET /api/hx-edr-prompt —— 「复制 EDR 送审提示词」正文。"""
        text = hx_prompt.edr_prompt()
        self._json({"version": hx_prompt.PROMPT_VERSION, "chars": len(text), "text": text})

    def get_hx_standards_template(self, m, query) -> None:
        """GET /api/hx-standards-template.zip —— 空模板(表头 + 2 行示例)。"""
        import zipfile as _zip

        base = standards.TEMPLATE_DIR
        if not base.is_dir():
            self._error("这份安装里没带模板目录(samples/hx-standards-template)", 404)
            return
        added = 0
        buf = io.BytesIO()
        with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as zf:
            for spec in standards.STD_FILES:
                path = base / spec["name"]
                if path.is_file():
                    zf.writestr(spec["name"], path.read_bytes())
                    added += 1
            note = base / "README.md"
            if note.is_file():
                zf.writestr("README.md", note.read_bytes())
        if not added:
            self._error("模板目录里没有可下载的 CSV", 404)
            return
        data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", 'attachment; filename="hx-standards-template.zip"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def get_hx_spec(self, m, query) -> None:
        """GET /api/hx-spec —— 口径常量:裕量、温压偏移、污垢表、经验 U、状态枚举。"""
        from app.design.heat import params as hx_params

        payload = {
            "margin": {"default": 0.30, "min": 0.30, "max": 0.50},
            "t_design_offset": {
                "default": hx_params.DESIGN_T_DEFAULT_OFFSET,
                "min": 15.0,
                "max": 30.0,
            },
            "fouling": hx_data.FOULING_DEFAULTS,
            "u_reference": hx_data.U_REFERENCE_DEFAULTS,
            "utility_presets": hx_data.UTILITY_PRESET_DEFAULTS,
            "tube_fluid_rules": list(hx_data.TUBE_FLUID_RULES),
            "pass_combinations": hx_data.pass_combinations(),
            "verdicts": [
                {"key": "blocked", "label": "已拦截"},
                {"key": "unknown", "label": "缺数据"},
                {"key": "reference", "label": "参考"},
                {"key": "pass", "label": "简捷通过"},
                {"key": "fail", "label": "简捷未通过"},
            ],
            "blocker_text": hx_params.BLOCKER_TEXT,
            "warning_text": hx_params.WARNING_TEXT,
            "std_state": standards.standard_state(),
            "estimate": {
                "tube_ods_mm": list(hx_data.ESTIMATE_TUBE_ODS_MM),
                "lengths_mm": list(hx_data.ESTIMATE_LENGTHS_MM),
            },
            "props_guide": hx_data.props_setup_guide(),
        }
        self._json(payload)

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

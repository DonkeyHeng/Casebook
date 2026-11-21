"""工况域:列表、明细、流股 / 设备 / 拓扑、工段边界、组成、删除。

这一层只管"请求怎么变成一次 db 调用",算法都在 `app.design` / `app.store`;
方法都挂在 `Handler` 上(见 `routes/__init__.py` 的 mixin 组装),所以直接用
`self._json` / `self._error`。
"""

from __future__ import annotations

import json

from app import component_names
from app.api.serialize import row_to_dict
from app.store import db, flowsheet, impact


class CaseRoutes:
    """工况域的路由与处理。"""

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
            self._json(
                {
                    "ok": True,
                    "deleted": case_id,
                    "model_name": case["model_name"],
                    "read_at": case["read_at"],
                    "removed": removed,
                }
            )
        finally:
            conn.close()

    def _inherit_sections(self, raw_id: str) -> None:
        """把同模型更早工况保存过的工段边界搬过来,省掉重新拉一遍。

        只在当次请求里搬 —— 搬完写进 data/config/sections.json 的当前工况键下,
        之后照常可编辑、可保存。源工况没有保存过边界就报 404,不去猜。
        """
        from app.store import sections_config

        try:
            case_id = int(raw_id)
        except ValueError:
            self._error("工况 id 必须是整数")
            return
        conn = db.open_db(self.db_path)
        try:
            if conn.execute("SELECT 1 FROM cases WHERE id = ?", (case_id,)).fetchone() is None:
                self._error(f"没有 id={case_id} 的工况", 404)
                return
            prev_case = db.previous_model_case(conn, case_id)
        finally:
            conn.close()
        if prev_case is None:
            self._error("同一模型没有更早的工况可以沿用", 404)
            return
        prev = prev_case["case_id"]
        parts, overrides, _deleted, err = sections_config.load(prev)
        if err:
            self._error(err, 500)
            return
        if not parts and not overrides:
            self._error(f"工况 #{prev} 没有保存过工段边界,无从沿用", 404)
            return
        ok, save_errors = sections_config.save(case_id, parts, overrides)
        self._json(
            {
                "ok": ok,
                "from_case": prev,
                "match": prev_case["match"],
                "errors": save_errors,
                "zones": sorted(parts.keys()),
                "config": sections_config.status(),
            },
            200 if ok else 500,
        )

    def _set_component_cas(self, raw_id: str) -> None:
        """手动填写某个组分的 CAS 号;body 里 cas 为空表示清除。

        为什么按**模型**存、不写回 components 表:
        组分是模型的属性,而且重读工况会整份重建 components(= 手填会被冲掉)。
        手填只补空缺 —— 该组分从 Aspen / 模型文件读得到 CAS 时,以读数为准。
        """
        try:
            case_id = int(raw_id)
        except ValueError:
            self._error("工况 id 必须是整数")
            return
        body = self._read_json_body()
        component = str(body.get("component") or "").strip()
        if not component:
            self._error("component 不能为空")
            return
        raw = str(body.get("cas") or "").strip()
        cas = None
        if raw:
            cas = component_names.clean_cas(raw)
            if not cas:
                self._error("CAS 号形状不对:应为 数字-数字-数字 的形式,例如 2134-29-4")
                return
        conn = db.open_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT id, model_name FROM cases WHERE id = ?", (case_id,)
            ).fetchone()
            if row is None:
                self._error(f"没有 id={case_id} 的工况", 404)
                return
            key = db.model_key(conn, case_id)
            if not key:
                self._error("这个工况没有可用的模型身份,无法挂手填 CAS")
                return
            db.save_component_cas(conn, key, component, cas)
        finally:
            conn.close()
        self._json(
            {
                "ok": True,
                "case_id": case_id,
                "model_name": row["model_name"],
                "component": component,
                "cas": cas,
                "cn": component_names.cn_name(cas) if cas else None,
            }
        )

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

    def _sections(self, conn, case_id: int) -> None:
        """GET /api/cases/{id}/sections —— 工段(界区)边界:草稿 + 已确认 + 合并。"""
        from app.design import sections as section_rules
        from app.store import sections_config

        blocks = [dict(r) for r in db.read_blocks(conn, case_id)]
        connections = [dict(r) for r in db.read_connections(conn, case_id)]
        draft = section_rules.draft(blocks, connections)
        confirmed, overrides, deleted, error = sections_config.load(case_id)
        self._json(
            {
                "case_id": case_id,
                "draft": draft,
                "confirmed": confirmed,
                "deleted": deleted,
                "merged": section_rules.merge(draft, confirmed, deleted),
                "auto": not confirmed,
                "device_sections": overrides,
                "config": sections_config.status(),
                "error": error,
            }
        )

    def _save_sections(self, raw_id: str) -> None:
        """POST /api/cases/{id}/sections —— 保存边界(只写 data/config/,不碰工况库)。"""
        from app.store import sections_config

        try:
            case_id = int(raw_id)
        except ValueError:
            self._error("工况 id 必须是整数")
            return
        parts, overrides, deleted, errors = sections_config.validate(self._read_json_body())
        if errors:
            self._json({"ok": False, "errors": errors}, 400)
            return
        ok, save_errors = sections_config.save(case_id, parts, overrides, deleted)
        payload = {"ok": ok, "errors": save_errors, "config": sections_config.status()}
        self._json(payload, 200 if ok else 500)

    def get_cases(self, m, query):
        """GET /api/cases —— 工况列表。"""
        conn = db.open_db(self.db_path)
        try:
            self._json([row_to_dict(r) for r in db.list_cases(conn)])
        finally:
            conn.close()

    def get_case(self, conn, case, m, query):
        """GET /api/cases/{id} —— 单个工况详情(首页仪表盘要用连接条数与焓平衡四态)。"""
        payload = row_to_dict(case)
        payload["connection_count"] = len(db.read_connections(conn, int(m["case_id"])))
        payload["balance_summary"] = db.balance_summary(conn, int(m["case_id"]))
        for key in (
            "reasons_json",
            "status_json",
            "tear_json",
            "block_errors",
            "block_warnings",
        ):
            if payload.get(key):
                try:
                    payload[key.replace("_json", "")] = json.loads(payload[key])
                except (TypeError, ValueError):
                    pass
        self._json(payload)

    def get_case_streams(self, conn, case, m, query):
        """GET /api/cases/{id}/streams —— 流股表。"""
        self._json([row_to_dict(r) for r in db.read_streams(conn, int(m["case_id"]))])

    def get_case_composition(self, conn, case, m, query):
        """GET /api/cases/{id}/streams/{流股}/composition —— 按需读单条流股组成。"""
        self._stream_composition(conn, int(m["case_id"]), m["stream"])

    def get_case_blocks(self, conn, case, m, query):
        """GET /api/cases/{id}/blocks —— 设备表(家族现算,老工况不用迁移)。

        泵块额外补 `motor_suggested_kw` 与功率口径:与泵选型页、Excel、AI 包同源,
        页面表格不再回落到 Aspen 轴功率口径的旧 `motor_kw`。
        """
        from app.design import block_family, pump_line_context

        case_id = int(m["case_id"])
        blocks = [dict(row) for row in db.read_blocks(conn, case_id)]
        streams = [dict(row) for row in db.read_streams(conn, case_id)]
        connections = [dict(row) for row in db.read_connections(conn, case_id)]
        rows = []
        for item in blocks:
            item["family"] = block_family.family_of_block(item)
            if item["family"] == "pump":
                try:
                    lines = pump_line_context.lines_for(
                        conn,
                        case_id,
                        item,
                        blocks=blocks,
                        streams=streams,
                        connections=connections,
                    )
                    motor = (lines.get("totals") or {}).get("motor") or {}
                    item["motor_suggested_kw"] = motor.get("kw")
                    item["motor_power_source"] = motor.get("power_source")
                    item["motor_basis_kw"] = motor.get("basis_kw")
                    item["motor_margin"] = motor.get("margin")
                    item["motor_note"] = motor.get("note")
                except Exception:  # 单台泵算不出不该让整张设备表打不开
                    item["motor_suggested_kw"] = item.get("motor_kw")
                    item["motor_power_source"] = "Aspen轴功率（回退）"
                    item["motor_basis_kw"] = None
                    item["motor_margin"] = None
                    item["motor_note"] = "泵两侧管段计算失败，暂按 Aspen 轴功率列值"
            rows.append(item)
        self._json(rows)

    def get_case_connections(self, conn, case, m, query):
        """GET /api/cases/{id}/connections —— 设备 ↔ 流股连接关系。"""
        self._json([row_to_dict(r) for r in db.read_connections(conn, int(m["case_id"]))])

    def get_case_flowsheet(self, conn, case, m, query):
        """GET /api/cases/{id}/flowsheet —— 流程图数据(节点 = 设备,边 = 流股)。"""
        case_id = int(m["case_id"])
        self._json(
            flowsheet.build(
                [dict(r) for r in db.read_connections(conn, case_id)],
                [dict(r) for r in db.read_blocks(conn, case_id)],
            )
        )

    def get_case_impact(self, conn, case, m, query):
        """GET /api/cases/{id}/impact —— 报错设备的波及范围。"""
        case_id = int(m["case_id"])
        self._json(
            impact.build(
                [dict(r) for r in db.read_connections(conn, case_id)],
                [dict(r) for r in db.read_blocks(conn, case_id)],
            )
        )

    def get_case_columns(self, conn, case, m, query):
        """GET /api/cases/{id}/columns —— 塔列表。"""
        self._json([row_to_dict(r) for r in db.list_columns(conn, int(m["case_id"]))])

    def get_case_column_stages(self, conn, case, m, query):
        """GET /api/cases/{id}/columns/{块名} —— 塔逐板剖面。"""
        self._json(
            [row_to_dict(r) for r in db.read_column_stages(conn, int(m["case_id"]), m["block"])]
        )

    def delete_case(self, m, query):
        """DELETE /api/cases/{id} —— 删掉一个工况及其明细。"""
        self._delete_case(m["case_id"])

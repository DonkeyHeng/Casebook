"""单条管段记录接口(泵两侧 ↔ 管道页共用)。

* `GET /api/cases/{id}/lines/{stream}` —— 这条管子现在的规格、输入、实时 Σhf;
* `POST /api/cases/{id}/lines/{stream}` —— 保存输入(管长 / Σζ / 管件 / ΔZ /
  人工 DN / 手填粘度),带 `version` 做乐观锁。

为什么单独一个文件:管段记录不属于泵,也不属于管道页 —— 两边都只是视图。
路由表里加行,处理函数在这里,别往 `do_POST` 里塞分支。
"""

from __future__ import annotations

import json

from app.design import pump_line, pump_line_context
from app.store import db


def _open_case(db_path, raw_id):
    try:
        case_id = int(raw_id)
    except (TypeError, ValueError):
        return None, None, None
    conn = db.open_db(db_path)
    case = db.get_case(conn, case_id)
    return conn, case, case_id


class LineRoutes:
    """管段记录与管道页参数的读写。"""

    # ------------------------------------------------------------ 单条管段
    def get_line(self, conn, case, m, query):
        """GET /api/cases/{id}/lines/{stream}"""
        case_id = int(case["id"])
        stream = str(m["stream"])
        payload = self._line_payload(conn, case_id, stream)
        if payload is None:
            self._error(f"工况里没有流股 {stream}", 404)
            return
        self._json(payload)

    def post_line(self, m, query):
        """POST /api/cases/{id}/lines/{stream} —— 保存管段输入(带乐观锁)。"""
        conn, case, case_id = _open_case(self.db_path, m["case_id"])
        if conn is None or case is None:
            self._error("没有这个工况", 404)
            if conn is not None:
                conn.close()
            return
        try:
            self._save_line(conn, case_id, str(m["stream"]))
        finally:
            conn.close()

    def _save_line(self, conn, case_id: int, stream: str):
        body = self._read_json_body()
        try:
            values, expected = self._validate_run(body)
        except ValueError as exc:
            self._error(str(exc))
            return
        # 先确认流股存在,别给拼错的流股名留下一条孤儿记录
        names = {str(dict(r).get("name")) for r in db.read_streams(conn, case_id)}
        if stream not in names:
            self._error(f"工况里没有流股 {stream}", 404)
            return

        key = db.model_key(conn, case_id)
        try:
            saved, version = db.save_pipe_run(conn, key, stream, values, expected)
        except db.VersionConflict as exc:
            self._error(
                f"这条管段已被别处修改(当前版本 {exc.current_version}),请刷新后再存",
                409,
            )
            return

        payload = self._line_payload(conn, case_id, stream)
        if payload is None:
            self._error(f"工况里没有流股 {stream}", 404)
            return
        payload["saved"] = saved
        payload["version"] = version
        self._json(payload)

    def _validate_run(self, body: dict) -> tuple[dict, int]:
        """校验并归一化一条管段输入;缺项写 None,不补默认值。"""

        def num(key: str, lo: float, hi: float):
            raw = body.get(key)
            if raw in (None, ""):
                return None
            if isinstance(raw, bool):
                raise ValueError(f"{key} 必须是数字")
            try:
                val = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} 必须是数字") from exc
            if not lo <= val <= hi:
                raise ValueError(f"{key} 超出合理范围({lo}~{hi})")
            return val

        values = {
            "l_m": num("l_m", 0.0, 100000.0),
            "zeta_sum": num("zeta_sum", 0.0, 10000.0),
            "dz_m": num("dz_m", -10000.0, 10000.0),
            "mu_cp": num("mu_cp", 0.0001, 1e6),
            "lambda_manual": num("lambda_manual", 0.0001, 1.0),
            "note": str(body.get("note") or "").strip() or None,
        }

        method = str(body.get("lambda_method") or "").strip().lower()
        if method and method not in ("colebrook", "gu", "manual"):
            raise ValueError("lambda_method 只能是 colebrook / gu / manual")
        values["lambda_method"] = method or None

        fittings = body.get("fittings")
        if fittings is None and body.get("fittings_json"):
            try:
                fittings = json.loads(body["fittings_json"])
            except (TypeError, ValueError) as exc:
                raise ValueError("fittings_json 不是合法 JSON") from exc
        if fittings is not None:
            if not isinstance(fittings, dict):
                raise ValueError("fittings 必须是对象:{管件代号: 数量}")
            clean = {}
            for k, v in fittings.items():
                try:
                    qty = float(v or 0)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"管件 {k} 的数量必须是数字") from exc
                if qty < 0 or qty > 10000:
                    raise ValueError(f"管件 {k} 的数量超出合理范围")
                if qty:
                    clean[str(k)] = int(qty) if qty.is_integer() else qty
            values["fittings_json"] = (
                json.dumps(clean, ensure_ascii=False, separators=(",", ":")) if clean else None
            )
        else:
            values["fittings_json"] = None

        dn = body.get("dn_override")
        if dn in (None, ""):
            values["dn_override"] = None
        else:
            try:
                dn_val = int(dn)
            except (TypeError, ValueError) as exc:
                raise ValueError("dn_override 必须是整数 DN") from exc
            from app.design.pipe import data as pipe_data

            if dn_val not in pipe_data.DN_TO_OD:
                raise ValueError(f"DN{dn_val} 不在已录入的 DN 系列里;超出系列请填自定义外径与壁厚")
            values["dn_override"] = dn_val

        od = num("od_override_mm", 1.0, 5000.0)
        wall = num("wall_override_mm", 0.1, 100.0)
        if (od is None) != (wall is None):
            raise ValueError("自定义外径与壁厚必须同时填写")
        values["od_override_mm"] = od
        values["wall_override_mm"] = wall
        if values["dn_override"] is None and od is None and wall is None:
            pass
        elif values["dn_override"] is not None and od is not None:
            raise ValueError("人工 DN 与自定义外径只能二选一")

        expected = body.get("version")
        try:
            expected = int(expected or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("version 必须是整数") from exc
        if expected < 0:
            raise ValueError("version 不能为负")
        return values, expected

    def _line_payload(self, conn, case_id: int, stream: str) -> dict | None:
        streams = [dict(r) for r in db.read_streams(conn, case_id)]
        if not any(s.get("name") == stream for s in streams):
            return None
        blocks = [dict(r) for r in db.read_blocks(conn, case_id)]
        conns = [dict(r) for r in db.read_connections(conn, case_id)]

        users = [
            b
            for b in blocks
            if any(c.get("stream") == stream and c.get("block") == b.get("name") for c in conns)
        ]
        block = users[0] if users else {"name": "", "mu_cp": None}
        roles = []
        for b in users or [block]:
            for direction in ("in", "out"):
                if any(
                    c.get("stream") == stream
                    and c.get("block") == b.get("name")
                    and str(c.get("direction")) == direction
                    for c in conns
                ):
                    roles.append("suction" if direction == "in" else "discharge")
        role = roles[0] if roles else "suction"

        options = pump_line_context.options_for(conn, case_id)
        rows, missing, tables_error = pump_line_context._pipe_rows(
            conn,
            case_id,
            blocks=blocks,
            streams=streams,
            connections=conns,
            options=options,
        )
        found = db.find_pipe_run(conn, case_id, stream)
        run = found["row"] or {}
        line = pump_line.compute_line(
            role,
            {"status": pump_line.ST_LINKED, "stream": stream, "reason": ""},
            pipe_rows=rows,
            pipe_missing=missing,
            streams=streams,
            block=block,
            run=run,
            eps_mm=options.get("roughness_mm"),
        )
        pipe_row = next((r for r in rows if r.get("stream") == stream), None)
        return {
            "ok": True,
            "stream": stream,
            "line": line,
            "role": role,
            "used_by": [b.get("name") for b in users],
            "run": run,
            "version": int(run.get("version") or 0),
            "source": found.get("source"),
            "pipe_row": pipe_row,
            "tables_error": tables_error,
            "options": options,
        }

    # ------------------------------------------------------------ 管道页参数
    def get_pipe_settings(self, conn, case, m, query):
        """GET /api/cases/{id}/pipe-settings —— 该模型存下来的管道页参数。"""
        case_id = int(case["id"])
        key = db.model_key(conn, case_id)
        stored = db.read_pipe_options(conn, key)
        self._json(
            {
                "ok": True,
                "options": pump_line_context._merge_options(stored),
                "stored": stored is not None,
            }
        )

    def post_pipe_settings(self, m, query):
        """POST /api/cases/{id}/pipe-settings —— 保存参数;`dry_run` 只回影响预览。

        改参数会影响所有泵的扬程与 NPSH(经管径变化),所以先算一遍:
        影响几台泵、设计扬程最大变化多少;确认后前端再带 `dry_run=false` 写库。
        """
        conn, case, case_id = _open_case(self.db_path, m["case_id"])
        if conn is None or case is None:
            self._error("没有这个工况", 404)
            if conn is not None:
                conn.close()
            return
        try:
            self._save_pipe_settings(conn, case_id)
        finally:
            conn.close()

    # ------------------------------------------------------------ 管道页参数
    def _validate_options(self, raw: dict) -> dict:
        """校验管道页参数:流速 / 压力系数 / 最大 DN / 牌号,缺项沿用默认。"""
        base = pump_line_context.default_pipe_options()
        velocity_raw = raw.get("velocity") or {}
        if not isinstance(velocity_raw, dict):
            raise ValueError("velocity 必须是对象")
        velocity = dict(base["velocity"])
        for k, v in velocity_raw.items():
            if k not in velocity:
                raise ValueError(f"未知流速项 {k}")
            if v in (None, ""):
                continue
            try:
                val = float(v)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"流速 {k} 必须是数字") from exc
            if not 0.01 <= val <= 100.0:
                raise ValueError(f"流速 {k} 超出合理范围(0.01~100 m/s)")
            velocity[k] = val

        def num(key: str, lo: float, hi: float, cast=float):
            v = raw.get(key, base.get(key))
            if v in (None, ""):
                return base.get(key)
            try:
                val = cast(v)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} 必须是数字") from exc
            if not lo <= val <= hi:
                raise ValueError(f"{key} 超出合理范围({lo}~{hi})")
            return val

        material = str(raw.get("material") or base.get("material") or "").strip()
        from app.design.pipe import data as pipe_data

        available = pipe_data.piping_class_grades()
        if material and available and material not in available:
            raise ValueError(f"牌号 {material!r} 不在已录入的表 B.1 里")
        return {
            "velocity": velocity,
            "pressure_factor": num("pressure_factor", 0.5, 10.0),
            "pressure_add_mpa": num("pressure_add_mpa", 0.0, 10.0),
            "pressure_floor_mpa": num("pressure_floor_mpa", 0.0, 100.0),
            "max_dn": int(num("max_dn", 1, 10000, int)),
            "material": material or base["material"],
        }

    def _save_pipe_settings(self, conn, case_id: int):
        body = self._read_json_body()
        raw = body.get("options")
        if not isinstance(raw, dict):
            self._error("options 必须是对象")
            return
        try:
            options = self._validate_options(raw)
        except ValueError as exc:
            self._error(str(exc))
            return

        key = db.model_key(conn, case_id)
        before = pump_line_context._merge_options(db.read_pipe_options(conn, key))
        freeze = pump_line_context.freeze_state(conn, case_id)
        preview = self._impact(conn, case_id, before, options)
        dry_run = bool(body.get("dry_run"))
        if not dry_run:
            db.save_pipe_options(conn, key, options)
        self._json(
            {
                "ok": True,
                "dry_run": dry_run,
                "saved": not dry_run,
                "options": options,
                "impact": preview,
                "freeze": freeze,
            }
        )

    # ------------------------------------------------------------ 旧 Σhf 迁移
    def get_pump_lines_migration(self, conn, case, m, query):
        """GET /api/cases/{id}/pump-lines/migration —— 哪些泵还在用旧单一 Σhf。"""
        case_id = int(case["id"])
        blocks = [dict(r) for r in db.read_blocks(conn, case_id)]
        candidates = pump_line_context.legacy_candidates(conn, case_id, blocks)
        self._json({"ok": True, "candidates": candidates, "count": len(candidates)})

    def post_pump_lines_migrate(self, m, query):
        """POST /api/cases/{id}/pump-lines/migrate —— 把旧 Σhf 归到吸入/排出/合计。

        旧数据只有一组 L / Σζ / ΔZ,没有两侧之分。这里只做**归属登记**:
        * suction / discharge:把这组输入写到该侧流股的管段记录;
        * total:登记为"合计",不拆 —— 修正 NPSHₐ 判 unknown(D7),不假装知道分布。

        旧 ΔZ 是两侧合计,不硬塞给一侧;旧内径(若曾手填)会在响应里点名,
        提醒它没有沿用 —— 管径现在由管道页统一选。
        """
        conn, case, case_id = _open_case(self.db_path, m["case_id"])
        if conn is None or case is None:
            self._error("没有这个工况", 404)
            if conn is not None:
                conn.close()
            return
        try:
            self._migrate(conn, case_id)
        finally:
            conn.close()

    def _migrate(self, conn, case_id: int):
        body = self._read_json_body()
        block_name = str(body.get("block") or "").strip()
        split = str(body.get("split") or "").strip()
        if split not in ("suction", "discharge", "total"):
            self._error("split 只能是 suction / discharge / total")
            return
        blocks = [dict(r) for r in db.read_blocks(conn, case_id)]
        block = next((b for b in blocks if b.get("name") == block_name), None)
        if block is None:
            self._error(f"没有设备 {block_name}", 404)
            return
        legacy = pump_line_context.legacy_of(conn, case_id, block_name)
        if legacy is None:
            self._error(f"{block_name} 没有可迁移的旧管段数据", 404)
            return

        total_hf = legacy.get("hf_m")

        warnings = []
        stream = None
        if split in ("suction", "discharge"):
            direction = "in" if split == "suction" else "out"
            side = pump_line.stream_side(
                [dict(c) for c in db.read_connections(conn, case_id)], block_name, direction
            )
            if side["status"] == pump_line.ST_MISSING:
                self._error(side["reason"], 409)
                return
            if side["status"] == pump_line.ST_AMBIGUOUS:
                self._error(side["reason"] + ";先指定流股再迁移", 409)
                return
            stream = side["stream"]

            values = {
                "l_m": legacy.get("l_m"),
                "zeta_sum": legacy.get("zeta_sum"),
                "fittings_json": (
                    json.dumps(legacy.get("fittings"), ensure_ascii=False, separators=(",", ":"))
                    if legacy.get("fittings")
                    else None
                ),
                "dz_m": None,
                "note": (
                    f"旧单一 Σhf={total_hf:g} m 已归到{'吸入' if split == 'suction' else '排出'}侧"
                    if isinstance(total_hf, (int, float))
                    else "旧 Σhf 归到本侧"
                ),
            }
            found = db.find_pipe_run(conn, case_id, stream)
            expected = int(found["row"].get("version") or 0) if found["via"] == "current" else 0
            try:
                db.save_pipe_run(conn, db.model_key(conn, case_id), stream, values, expected)
            except db.VersionConflict as exc:
                self._error(f"管段记录已被修改(当前版本 {exc.current_version}),请刷新后重试", 409)
                return
            warnings.append("旧 ΔZ 是两侧合计,已置空;请在吸入 / 排出两行分别填标高差")
            if legacy.get("legacy_id_mm"):
                warnings.append(
                    f"旧手填内径 {legacy['legacy_id_mm']:g} mm 未沿用:管径改由管道页统一选型"
                )
        else:
            warnings.append("按「合计」登记:只用于对账,修正 NPSHₐ 仍判 unknown")

        db.save_legacy_split(conn, db.model_key(conn, case_id), block_name, split, total_hf)
        payload = {
            "ok": True,
            "block": block_name,
            "split": split,
            "stream": stream,
            "warnings": warnings,
            "legacy": legacy,
        }
        if stream:
            payload["line"] = self._line_payload(conn, case_id, stream)["line"]
        self._json(payload)

    def _impact(self, conn, case_id: int, before: dict, after: dict) -> dict:
        """改参数对泵扬程的影响:台数、最大变化、明细。"""
        streams = [dict(r) for r in db.read_streams(conn, case_id)]
        blocks = [dict(r) for r in db.read_blocks(conn, case_id)]
        conns = [dict(r) for r in db.read_connections(conn, case_id)]
        from app.design import block_family

        pumps = [b for b in blocks if block_family.family_of_block(b) == "pump"]
        changes = []
        max_delta = 0.0
        for block in pumps:
            saved = db.find_pump_selection(conn, case_id, block["name"])["row"]
            old = pump_line_context.lines_for(
                conn,
                case_id,
                block,
                blocks=blocks,
                streams=streams,
                connections=conns,
                options=before,
                saved=saved,
            )
            new = pump_line_context.lines_for(
                conn,
                case_id,
                block,
                blocks=blocks,
                streams=streams,
                connections=conns,
                options=after,
                saved=saved,
            )
            old_h = old["totals"].get("design_head_m")
            new_h = new["totals"].get("design_head_m")
            delta = None if old_h is None or new_h is None else round(new_h - old_h, 3)
            if delta is not None:
                max_delta = max(max_delta, abs(delta))
            changed = any(
                (old.get(side) or {}).get("dn") != (new.get(side) or {}).get("dn")
                for side in ("suction", "discharge")
            )
            if changed or (delta is not None and abs(delta) > 1e-9):
                changes.append(
                    {
                        "block": block["name"],
                        "old_head_m": old_h,
                        "new_head_m": new_h,
                        "delta_head_m": delta,
                        "old_dn": {
                            "suction": (old.get("suction") or {}).get("dn"),
                            "discharge": (old.get("discharge") or {}).get("dn"),
                        },
                        "new_dn": {
                            "suction": (new.get("suction") or {}).get("dn"),
                            "discharge": (new.get("discharge") or {}).get("dn"),
                        },
                    }
                )
        return {
            "pumps_total": len(pumps),
            "pumps_changed": len(changes),
            "max_delta_head_m": round(max_delta, 3),
            "changes": changes,
        }

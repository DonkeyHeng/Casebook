"""泵域:设计扬程、水力、p_v、选型台账。

口径在 `app/design/pump.py` 与 `docs/pump-selection-plan.md`;这里只负责把
库里的数读出来、把用户填的存回去。
"""

from __future__ import annotations

import json
import math

from app.design import hydraulics
from app.design import pipe as pipe_calc
from app.design import pump_selection as selection_rules
from app.store import db


class PumpRoutes:
    """泵域的路由与处理。"""

    def _set_pump_pv(self, raw_id: str, block: str) -> None:
        """保存一台泵的饱和蒸气压(bar);body 里 pv_bar 为 null 表示清空。

        读取工况时会自动填 Aspen 进口流股的泡点压力,这里只用于**覆盖 / 补空**:
        例如 Aspen 没给读数,或用户要改用厂家实测值。程序本身不估算 p_v。
        """
        try:
            case_id = int(raw_id)
        except ValueError:
            self._error("工况 id 必须是整数")
            return
        body = self._read_json_body()
        raw = body.get("pv_bar")
        pv = None
        if raw not in (None, ""):
            try:
                pv = float(raw)
            except (TypeError, ValueError):
                self._error("pv_bar 必须是数字")
                return
            if not 0.0 <= pv <= 500.0:
                self._error("pv_bar 超出合理范围(0~500 bar)")
                return
        conn = db.open_db(self.db_path)
        try:
            if not db.set_pump_pv(conn, case_id, block, pv, source="manual"):
                self._error(f"没有设备 {block}(工况 {case_id})", 404)
                return
        finally:
            conn.close()
        self._json(
            {
                "ok": True,
                "case_id": case_id,
                "block": block,
                "pv_bar": pv,
                "pv_source": "manual" if pv is not None else None,
            }
        )

    def _pump_design(self, raw_id: str, block: str) -> None:
        """GET …/pumps/{位号}/design-head —— 手填的设计扬程修正项与管路输入。

        zeta_json 在库里是文本,这里解析成对象;坏 JSON 只让这一项为空,
        不让 ΔZ / Σhf / 管长跟着一起读不出来。
        """
        try:
            case_id = int(raw_id)
        except ValueError:
            self._error("工况 id 必须是整数")
            return
        conn = db.open_db(self.db_path)
        try:
            row = conn.execute("SELECT id FROM cases WHERE id = ?", (case_id,)).fetchone()
            if row is None:
                self._error(f"没有 id={case_id} 的工况", 404)
                return
            data = dict(
                db.read_pump_design(conn, db.model_key(conn, case_id), block)
                or {"dz_m": None, "hf_m": None, "l_m": None, "zeta_json": None}
            )
        finally:
            conn.close()
        raw = data.get("zeta_json")
        try:
            data["zeta_json"] = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            data["zeta_json"] = None
        self._json({"ok": True, "block": block, **data})

    def _pump_hydraulics(self, raw_id: str, block_name: str) -> None:
        """GET …/pumps/{位号}/hydraulics —— 手算 Σhf 需要的输入(含管道选型内径)。

        内径优先取「管道」页同一套选型结果(od − 2×wall);标准表没录全时给空,
        让用户手填 —— 不做假设性默认值。
        """
        from app.design import hydraulics
        from app.design import pipe as pipe_calc

        try:
            case_id = int(raw_id)
        except ValueError:
            self._error("工况 id 必须是整数")
            return
        conn = db.open_db(self.db_path)
        try:
            block = next(
                (dict(b) for b in db.read_blocks(conn, case_id) if b["name"] == block_name), None
            )
            if block is None:
                self._error(f"没有设备 {block_name}(工况 {case_id})", 404)
                return
            conns = [dict(c) for c in db.read_connections(conn, case_id)]
            inlet = next(
                (
                    c["stream"]
                    for c in conns
                    if c["block"] == block_name and str(c.get("direction")) == "in"
                ),
                None,
            )
            dn = od = wall = id_mm = None
            id_note = ""
            if inlet:
                try:
                    result = pipe_calc.build(
                        [dict(r) for r in db.read_streams(conn, case_id)],
                        [dict(b) for b in db.read_blocks(conn, case_id)],
                        conns,
                    )
                    row = next(
                        (r for r in result.get("rows", []) if r.get("stream") == inlet), None
                    )
                    if row:
                        dn, od, wall = row.get("dn"), row.get("od_mm"), row.get("wall_mm")
                        if od and wall:
                            id_mm = round(od - 2 * wall, 2)
                        else:
                            # 标准档位里没有合适规格(内径超 DN600 等),管道页也是这么记的
                            id_note = str(row.get("reason") or "")
                    else:
                        miss = result.get("missing") or []
                        hit = next(
                            (
                                m
                                for m in miss
                                if isinstance(m, dict) and m.get("stream") == inlet
                            ),
                            None,
                        )
                        if hit:
                            id_note = str(hit.get("reason") or "")
                except Exception:
                    pass  # 标准表没录全 / 缺数据:不给默认内径,前端手填
        finally:
            conn.close()
        self._json(
            {
                "ok": True,
                "block": block_name,
                "inlet_stream": inlet,
                "flow_m3h": block.get("flow_m3h"),
                "rho_kgm3": block.get("rho_kgm3"),
                "mu_cp": block.get("mu_cp"),
                "dn": dn,
                "od_mm": od,
                "wall_mm": wall,
                "id_mm": id_mm,
                "id_note": id_note,
                "eps_default_mm": hydraulics.EPS_DEFAULT_MM,
            }
        )

    def _pump_head_loss(self, raw_id: str, block_name: str) -> None:
        """POST …/pumps/{位号}/head-loss —— 按管路参数算 Σhf(只算,不存)。

        需要 body 至少给 `l_m` 与 `zeta_sum`;内径/粘度/粗糙度可覆盖。返回 u / Re / λ / Σhf。
        缺什么就在 `missing` 里列出来,不返回半真半假的结果。
        """
        from app.design import hydraulics

        try:
            case_id = int(raw_id)
        except ValueError:
            self._error("工况 id 必须是整数")
            return
        body = self._read_json_body()

        def pick(key: str, lo: float, hi: float):
            raw = body.get(key)
            if raw in (None, ""):
                return None
            try:
                val = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} 必须是数字") from exc
            if not lo <= val <= hi:
                raise ValueError(f"{key} 超出合理范围({lo}~{hi})")
            return val

        try:
            l_m = pick("l_m", 0.0, 100000.0)
            zeta = pick("zeta_sum", 0.0, 10000.0)
            d_mm = pick("d_mm", 1.0, 5000.0)
            eps = pick("eps_mm", 0.0, 50.0)
            mu = pick("mu_cp", 0.0001, 1e6)
            lam_manual = pick("lam", 0.0001, 1.0)
        except ValueError as exc:
            self._error(str(exc))
            return
        method = str(body.get("method") or hydraulics.METHOD_COLEBROOK).strip().lower()
        if method not in (
            hydraulics.METHOD_COLEBROOK,
            hydraulics.METHOD_GU,
            hydraulics.METHOD_MANUAL,
        ):
            self._error("method 只能是 colebrook / gu / manual")
            return

        conn = db.open_db(self.db_path)
        try:
            block = next(
                (dict(b) for b in db.read_blocks(conn, case_id) if b["name"] == block_name), None
            )
        finally:
            conn.close()
        if block is None:
            self._error(f"没有设备 {block_name}(工况 {case_id})", 404)
            return

        result = hydraulics.solve(
            vol_m3h=block.get("flow_m3h"),
            rho_kgm3=block.get("rho_kgm3"),
            mu_cp=mu if mu is not None else block.get("mu_cp"),
            id_mm=d_mm,
            l_m=l_m,
            zeta_sum=zeta,
            eps_mm=eps,
            method=method,
            lam_manual=lam_manual,
        )
        # 缺项由 hydraulics.solve 统一报(它同时知道流量/密度/粘度/内径缺不缺),
        # 这里不再重复 append,否则同一个缺失项会出现两次
        self._json({"ok": True, "block": block_name, **result})

    def _set_pump_design(self, raw_id: str, block: str) -> None:
        """POST …/pumps/{位号}/design-head —— 存 ΔZ、Σhf 与算 Σhf 的人填输入。

        工具只负责 `H = 压升扬程 + ΔZ + Σhf` 的加法和 1.1 取整,**不估算这几项**
        (ΔZ 是标高几何,Σhf 要管长/管件/λ,都属于人工程序)。
        管长 L 与管件数量只做校验和持久化,不替用户补默认数量;全部为空 = 清除。
        λ 口径只存用户的白名单选择,不替用户挑公式。
        """
        from app.design import hydraulics   # 与其它接口一致,局部导入

        try:
            case_id = int(raw_id)
        except ValueError:
            self._error("工况 id 必须是整数")
            return
        body = self._read_json_body()

        def pick(key: str):
            raw = body.get(key)
            if raw in (None, ""):
                return None
            try:
                val = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} 必须是数字") from exc
            if not -1000.0 <= val <= 5000.0:
                raise ValueError(f"{key} 超出合理范围(-1000~5000 m)")
            return val

        # 与前端 PUMP_FITTINGS 的 key 对齐;前端加管件时这里要同步
        fitting_keys = ("el90", "gate", "globe", "check", "reducer")
        # 手填覆盖的自动取值:原本刷新即丢,现在随设计扬程一起存;缺项不补默认值
        override_keys = ("id_mm", "mu_cp", "eps_mm")

        def pick_zeta() -> str | None:
            """管件数量 + 手填 Σζ + 覆盖值 → JSON 文本;全空返回 None(不落空记录)。"""
            raw = body.get("zeta_json")
            if raw in (None, ""):
                return None
            if not isinstance(raw, dict):
                raise ValueError("zeta_json 必须是对象")
            zsum = raw.get("zeta_sum")
            if zsum is not None:
                if isinstance(zsum, bool) or not isinstance(zsum, (int, float)):
                    raise ValueError("zeta_sum 必须是数字")
                if not 0 <= float(zsum) <= 100000:
                    raise ValueError("zeta_sum 超出合理范围(0~100000)")
            fits = raw.get("fittings") or {}
            if not isinstance(fits, dict):
                raise ValueError("fittings 必须是对象")
            clean: dict[str, int] = {}
            for key, val in fits.items():
                if key not in fitting_keys:
                    raise ValueError(f"不认识的管件:{key}")
                if isinstance(val, bool) or not isinstance(val, int) or not 0 <= val <= 10000:
                    raise ValueError(f"{key} 的数量必须是 0~10000 的整数")
                clean[key] = val
            ov = raw.get("overrides") or {}
            if not isinstance(ov, dict):
                raise ValueError("overrides 必须是对象")
            clean_ov: dict[str, float] = {}
            for key, val in ov.items():
                if key not in override_keys:
                    raise ValueError(f"不认识的覆盖项:{key}")
                if val in (None, ""):
                    continue
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    raise ValueError(f"{key} 必须是数字")
                if not 0 < float(val) <= 100000:
                    raise ValueError(f"{key} 超出合理范围(0~100000]")
                clean_ov[key] = float(val)
            lam = raw.get("lambda") or {}
            if not isinstance(lam, dict):
                raise ValueError("lambda 必须是对象")
            method = str(lam.get("method") or hydraulics.METHOD_COLEBROOK).strip().lower()
            if method not in (
                hydraulics.METHOD_COLEBROOK,
                hydraulics.METHOD_GU,
                hydraulics.METHOD_MANUAL,
            ):
                raise ValueError("lambda.method 只能是 colebrook / gu / manual")
            lam_raw = lam.get("manual")
            if lam_raw in (None, ""):
                lam_manual = None
            elif isinstance(lam_raw, bool) or not isinstance(lam_raw, (int, float)):
                raise ValueError("lambda.manual 必须是数字")
            elif not 0.0001 <= float(lam_raw) <= 1.0:
                raise ValueError("lambda.manual 超出合理范围(0.0001~1.0)")
            else:
                lam_manual = float(lam_raw)
            # 默认口径 + 没手填值时不落这个 key,免得给每台泵都留一条空挂账
            clean_lam = (
                {"method": method, "manual": lam_manual}
                if method != hydraulics.METHOD_COLEBROOK or lam_manual is not None
                else {}
            )
            if zsum is None and not any(clean.values()) and not clean_ov and not clean_lam:
                return None
            payload = {"zeta_sum": zsum, "fittings": clean, "overrides": clean_ov}
            if clean_lam:
                payload["lambda"] = clean_lam
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        try:
            dz_m, hf_m = pick("dz_m"), pick("hf_m")
            l_m = pick("l_m")
            if l_m is not None and l_m < 0:
                raise ValueError("l_m 不能为负")
            zeta_json = pick_zeta()
        except ValueError as exc:
            self._error(str(exc))
            return
        conn = db.open_db(self.db_path)
        try:
            row = conn.execute("SELECT id FROM cases WHERE id = ?", (case_id,)).fetchone()
            if row is None:
                self._error(f"没有 id={case_id} 的工况", 404)
                return
            key = db.model_key(conn, case_id)
            if not key:
                self._error("这个工况没有可用的模型身份,无法挂手填值")
                return
            db.save_pump_design(conn, key, block, dz_m, hf_m, l_m, zeta_json)
        finally:
            conn.close()
        self._json(
            {
                "ok": True,
                "block": block,
                "dz_m": dz_m,
                "hf_m": hf_m,
                "l_m": l_m,
                "zeta_json": json.loads(zeta_json) if zeta_json else None,
            }
        )

    def _pump_selection(self, raw_id: str, block: str) -> None:
        """GET …/pumps/{位号}/selection —— 台账手填结果 + 实时设计点 + 三态。

        数值一律按当前工况实时算(不落库、不从外部 Excel 导入);台账只回
        人工决定的字段。三态里的「无法判定」必须写明缺什么。
        """
        from app.design import pump_selection as selection_rules

        try:
            case_id = int(raw_id)
        except ValueError:
            self._error("工况 id 必须是整数")
            return
        conn = db.open_db(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM blocks WHERE case_id = ? AND name = ?", (case_id, block)
            ).fetchone()
            if row is None:
                self._error(f"没有位号 {block} 的泵", 404)
                return
            key = db.model_key(conn, case_id)
            design = db.read_pump_design(conn, key, block)
            saved = db.read_pump_selection(conn, key, block)
            view = selection_rules.compute_view(dict(row), design, saved)
        finally:
            conn.close()
        self._json({"ok": True, "block": block, "saved": saved or {}, **view})

    def _set_pump_selection(self, raw_id: str, block: str) -> None:
        """POST …/pumps/{位号}/selection —— 整份提交人工决定的字段。

        不收任何计算值(流量 / 扬程 / NPSHₐ / 三态),那些由 GET 实时算;
        也就不存在「外部 Excel 数值进库」这条路径。
        """
        try:
            case_id = int(raw_id)
        except ValueError:
            self._error("工况 id 必须是整数")
            return
        body = self._read_json_body()
        text_fields = ("model_no", "pump_name", "material", "note")
        num_fields = ("motor_kw", "npshr_m", "margin_m")
        values: dict = {}
        try:
            for field in text_fields:
                raw = body.get(field)
                if raw in (None, ""):
                    values[field] = None
                elif not isinstance(raw, str):
                    raise ValueError(f"{field} 必须是文字")
                elif len(raw) > 200:
                    raise ValueError(f"{field} 太长(最多 200 字)")
                else:
                    values[field] = raw.strip()
            qty = body.get("qty")
            if qty in (None, ""):
                values["qty"] = None
            elif isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= 999:
                raise ValueError("qty 必须是 1~999 的整数")
            else:
                values["qty"] = qty
            for field in num_fields:
                raw = body.get(field)
                if raw in (None, ""):
                    values[field] = None
                    continue
                if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                    raise ValueError(f"{field} 必须是数字")
                if not 0 <= float(raw) <= 100000:
                    raise ValueError(f"{field} 超出合理范围(0~100000)")
                values[field] = float(raw)
        except ValueError as exc:
            self._error(str(exc))
            return
        conn = db.open_db(self.db_path)
        try:
            row = conn.execute("SELECT id FROM cases WHERE id = ?", (case_id,)).fetchone()
            if row is None:
                self._error(f"没有 id={case_id} 的工况", 404)
                return
            key = db.model_key(conn, case_id)
            if not key:
                self._error("这个工况没有可用的模型身份,无法挂台账")
                return
            db.save_pump_selection(conn, key, block, values)
        finally:
            conn.close()
        self._json({"ok": True, "block": block, "saved": values})

    def _inherit_pump_pv(self, raw_id: str) -> None:
        """把同一模型更早工况填过的 p_v 按位号沿用过来(只填空行,不覆盖手填值)。"""
        try:
            case_id = int(raw_id)
        except ValueError:
            self._error("工况 id 必须是整数")
            return
        conn = db.open_db(self.db_path)
        try:
            result = db.inherit_pump_pv(conn, case_id)
        finally:
            conn.close()
        if result is None:
            self._error(f"没有 id={case_id} 的工况", 404)
            return
        if result["source_case_id"] is None:
            self._error("没有可沿用的工况:需要同一模型、且更早的工况里填过 p_v", 404)
            return
        payload = {"ok": True, "case_id": case_id, **result}
        self._json(payload)

    def get_pump_spec(self, m, query):
        """GET /api/pump-spec —— 泵口径常量(前端算 NPSHₐ 与悬浮说明用)。"""
        from app.design import pump

        self._json(pump.spec())

    def get_pump_prompt(self, m, query):
        """GET /api/pump-prompt —— 「复制选泵提示词」的正文(唯一来源在 design)。"""
        from app.design import pump_prompt

        self._json(
            {"version": pump_prompt.PROMPT_VERSION, "text": pump_prompt.PUMP_SELECTION_PROMPT}
        )

    def get_pump_hydraulics(self, conn, case, m, query):
        """GET /api/cases/{id}/pumps/{块名}/hydraulics —— 管路阻力 Re / λ / Σhf。"""
        self._pump_hydraulics(m["case_id"], m["block"])

    def get_pump_design(self, conn, case, m, query):
        """GET /api/cases/{id}/pumps/{块名}/design-head —— 设计扬程辅助。"""
        self._pump_design(m["case_id"], m["block"])

    def get_pump_selection(self, conn, case, m, query):
        """GET /api/cases/{id}/pumps/{块名}/selection —— 选型台账。"""
        self._pump_selection(m["case_id"], m["block"])

    def post_pump_pv(self, m, query):
        """POST /api/cases/{id}/pumps/{块名}/pv —— 保存饱和蒸气压。"""
        self._set_pump_pv(m["case_id"], m["block"])

    def post_pump_design(self, m, query):
        """POST /api/cases/{id}/pumps/{块名}/design-head —— 保存设计扬程参数。"""
        self._set_pump_design(m["case_id"], m["block"])

    def post_pump_selection(self, m, query):
        """POST /api/cases/{id}/pumps/{块名}/selection —— 保存选型台账。"""
        self._set_pump_selection(m["case_id"], m["block"])

    def post_pump_head_loss(self, m, query):
        """POST /api/cases/{id}/pumps/{块名}/head-loss —— 保存管路阻力。"""
        self._pump_head_loss(m["case_id"], m["block"])

    def post_pump_inherit_pv(self, m, query):
        """POST /api/cases/{id}/pumps/inherit-pv —— 从同模型更早工况沿用 p_v。"""
        self._inherit_pump_pv(m["case_id"])

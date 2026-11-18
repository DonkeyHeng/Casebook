"""管路域:选型一览表、三张标准表的导入与状态、管道等级表、模板下载。

选型算法在 `app.design.pipe`;这里只做 HTTP 侧的事(校验、写盘、重新加载)。
"""

from __future__ import annotations

import io
import math
import zipfile

from app.api.errors import BadQuery
from app.api.serialize import pipe_table_status
from app.design import pipe as pipe_calc
from app.design.pipe import data as pipe_data
from app.design.pipe import prompt as pipe_standards_prompt
from app.store import db


class PipeRoutes:
    """管路域的路由与处理。"""

    def _std_template_zip(self) -> None:
        """GET /api/pipe-standards-template.zip —— 三份格式模板 + 说明打成一个 zip。

        文件从 `samples/pipe-standards-template/` 现读(唯一来源),不在代码里内嵌副本:
        模板改列名或加表,下载包自动跟着变。
        """

        from app.design.pipe import data as pipe_data

        base = pipe_data.TEMPLATE_DIR
        if not base.is_dir():
            self._error("这份安装里没带模板目录(samples/pipe-standards-template)", 404)
            return
        buf = io.BytesIO()
        added = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for spec in pipe_data.STD_FILES:
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
        self.send_header(
            "Content-Disposition", 'attachment; filename="pipe-standards-template.zip"'
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _pipes(self, conn, case_id: int, query: dict) -> None:
        """GET /api/cases/{id}/pipes —— 管路选型(实时算,不写库)。

        参数(都可省):`liquid` / `gas` / `two`(m/s)、`factor`(设计压力系数)、
        `floor`(设计压力下限 MPa)、`max_dn`、`material`(默认牌号)。
        标准表没录全时返回 409 并说明缺哪张,而不是吐一张空表。
        """
        from app.design.pipe import data as pipe_data

        # query 的值是 list(parse_qs 的行为),取第一个;不取的话 float(list) 会失败,
        # 参数就被静默换成默认值了(界面改了流速、结果却不变,就是这样来的)
        def _q(key: str):
            val = query.get(key)
            if isinstance(val, (list, tuple)):
                return val[0] if val else None
            return val

        def _f(key: str, default: float, lo: float, hi: float) -> float:
            """取一个数字参数:没给 → 默认值;给了但不是有限数或超范围 → 400。

            以前这里把任何解析失败都换成默认值:界面改了流速、结果却不变,
            用户以为是"算不动",其实是参数被悄悄丢掉了。NaN/Inf 还会一路
            传进选型计算。
            """
            raw = _q(key)
            if raw is None or str(raw).strip() == "":
                return default
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise BadQuery(f"参数 {key} 必须是数字,收到 {raw!r}") from None
            if not math.isfinite(value) or not lo <= value <= hi:
                raise BadQuery(f"参数 {key} 要在 {lo}~{hi} 之间,收到 {raw!r}")
            return value

        try:
            # 管材跟着**已录入的表 B.1** 走:表里有什么牌号就能选什么(方案 A)。
            # 以前这里把认不出的牌号静默换成 S30408,页面什么也不说;现在改成明确报错。
            available = pipe_data.piping_class_grades()
            default_material = (
                "S30408" if "S30408" in available else (available[0] if available else "S30408")
            )
            material = _q("material") or default_material
            if material not in available:
                raise BadQuery(
                    f"牌号 {material!r} 不在已录入的表 B.1 里;可选 {', '.join(available)}"
                    if available
                    else "还没有录入表 B.1,先导入标准表再选管材"
                )
            options = {
                "velocity": {
                    "liquid": _f("liquid", pipe_calc.DEFAULT_VELOCITY["liquid"], 0.01, 100.0),
                    "gas": _f("gas", pipe_calc.DEFAULT_VELOCITY["gas"], 0.01, 100.0),
                    "two": _f("two", pipe_calc.DEFAULT_VELOCITY["two"], 0.01, 100.0),
                    "pump_inlet": _f(
                        "pump_inlet", pipe_calc.DEFAULT_VELOCITY["pump_inlet"], 0.01, 100.0
                    ),
                },
                # §17.1/§17.2 口径:压力系数 1.15(原 1.1),DN 上限 1000(原 600)
                "pressure_factor": _f("factor", 1.15, 0.5, 10.0),
                "pressure_add_mpa": 0.15,
                "pressure_floor_mpa": _f("floor", 1.0, 0.0, 100.0),
                "max_dn": int(_f("max_dn", 1000, 1, 10000)),
                "material": material,
            }
        except BadQuery as exc:
            self._error(str(exc), 400)
            return

        streams = [dict(r) for r in db.read_streams(conn, case_id)]
        blocks = [dict(r) for r in db.read_blocks(conn, case_id)]
        conns = [dict(r) for r in db.read_connections(conn, case_id)]

        try:
            result = pipe_calc.build(streams, blocks, conns, options=options)
        except pipe_calc.TablesNotReady as exc:
            # 表没录不是错误,是"还没装数据"——把要做什么一并给出来,别只丢一句报错
            payload = {
                "error": str(exc),
                "tables": pipe_table_status(),
                "setup": pipe_data.setup_guide(),
            }
            self._json(payload, 409)
            return

        result["validation"] = pipe_calc.validate(result["rows"], options)
        result["options"] = options
        result["tables"] = pipe_table_status()
        self._json(result)

    def get_pipes(self, conn, case, m, query):
        """GET /api/cases/{id}/pipes —— 管路选型一览表。"""
        self._pipes(conn, int(m["case_id"]), query)

    def get_pipe_tables(self, m, query):
        """GET /api/pipe-tables —— 三张标准表录到什么程度(前端空状态用)。"""
        self._json(pipe_table_status())

    def get_pipe_standards(self, m, query):
        """GET /api/pipe-standards —— 三个 CSV 各自有没有、多少行、什么时候放进来的。"""
        files = pipe_data.standards_status()
        self._json({"dir": files["dir"], "files": files["files"], "status": pipe_table_status()})

    def post_pipe_standards(self, m, query):
        """POST /api/pipe-standards —— 导入标准表:校验 → 写盘 → 立即重新加载。"""
        ok, errors, files = pipe_data.import_standards(
            self._read_json_body(self.STANDARDS_BODY_LIMIT)
        )
        payload = {
            "ok": ok,
            "errors": errors,
            "dir": files["dir"],
            "files": files["files"],
            "status": pipe_table_status(),
        }
        if ok:
            payload["loaded"] = {
                "od_to_walls": len(pipe_data.OD_TO_WALLS),
                "od_to_walls_sst": len(pipe_data.OD_TO_WALLS_SST),
                "stress_grades": sorted(pipe_data.ALLOWABLE_STRESS),
            }
        self._json(payload, 200 if ok else 400)

    def get_pipe_standards_prompt(self, m, query):
        """GET /api/pipe-standards-prompt —— 「复制填写提示词」的正文。"""
        text = pipe_standards_prompt.build()
        self._json(
            {
                "version": pipe_standards_prompt.PROMPT_VERSION,
                "chars": len(text),
                "text": text,
            }
        )

    def get_pipe_standards_template(self, m, query):
        """GET /api/pipe-standards-template.zip —— 下载模板 CSV。"""
        self._std_template_zip()

    def get_piping_classes(self, m, query):
        """GET /api/piping-classes —— 管道等级表当前内容(「等级表」弹窗读它)。"""
        grades = pipe_data.piping_class_grades()
        self._json(
            {
                "status": pipe_data.piping_class_status(),
                "rules": pipe_data.PIPING_CLASS_RULES,
                "default": pipe_data.PIPING_CLASS_DEFAULT,
                "grades": list(grades),
                # 牌号 → 材质类别代号 + 来源(explicit / prefix / unknown);
                # prefix 是"按牌号前缀推断",下拉里会标「·推断」提醒核对
                "material_codes": {
                    g: dict(zip(("code", "source"), pipe_data.material_code_for(g), strict=False))
                    for g in grades
                },
                "media": list(pipe_data.PIPING_CLASS_MEDIA),
                "file": "data/config/piping-classes.json",
            }
        )

    def post_piping_classes(self, m, query):
        """POST /api/piping-classes —— 保存等级表:校验 → 写盘 → 重新加载。"""
        ok, errors = pipe_data.save_piping_classes(self._read_json_body())
        payload = {"ok": ok, "errors": errors, "status": pipe_data.piping_class_status()}
        self._json(payload, 200 if ok else 400)

    def delete_piping_classes(self, m, query):
        """DELETE /api/piping-classes —— 清空等级表(回到"全部按默认材质")。"""
        ok, errors = pipe_data.save_piping_classes({"rules": [], "default": None})
        self._json(
            {"ok": ok, "errors": errors, "status": pipe_data.piping_class_status()},
            200 if ok else 400,
        )

"""元信息域:健康检查、版本、读取任务、工况对比。

这些接口不挂在某个工况下面,前端进页面时先问一遍。
"""

from __future__ import annotations

from urllib.parse import quote

from app import paths, version
from app.api import read_job
from app.api.serialize import row_to_dict
from app.store import db

#: 两册《计算说明书》:页面上的「导出计算书」就下发这两份 Word,不另排一份
MANUALS: dict[str, dict[str, str]] = {
    "pipe": {
        "file": "casebook-pipe-calc-manual.docx",
        "title": "管路选型计算说明书",
        "ascii": "Casebook-pipe-calc-manual.docx",
    },
    "pump": {
        "file": "casebook-pump-calc-manual.docx",
        "title": "泵计算说明书",
        "ascii": "Casebook-pump-calc-manual.docx",
    },
    "hx": {
        "file": "casebook-hx-calc-manual.docx",
        "title": "换热器选型计算说明书",
        "ascii": "Casebook-hx-calc-manual.docx",
    },
}

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _attachment(name: str, ascii_name: str) -> str:
    """附件头:中文名走 RFC 5987,再给一个纯 ASCII 兜底名。

    `send_header` 只认 latin-1,中文直接写进 filename= 会在编码时抛错;
    `filename*=UTF-8''…` 由浏览器与桌面壳(desktop.Api._name_from)按 UTF-8 解回来。
    """
    return f"attachment; filename*=UTF-8''{quote(name)}; filename=\"{ascii_name}\""


class MetaRoutes:
    """元信息域的路由与处理。"""

    def _start_read(self) -> None:
        """POST /api/read —— 起一个后台读取任务,立刻返回当前状态。

        不让请求线程同步等 9–25 s:Aspen 的 COM 调用卡住时页面会一直转圈,
        用户看不出是"在算"还是"死了"。轮询能显示进度,也能显示卡在哪一步。
        """
        snap, started = read_job.start(
            self.db_path,
            include_composition=bool(self._read_json_body().get("include_composition")),
        )
        if not started:
            self._json(
                {
                    "ok": False,
                    "error": "已经有一次读取在进行中,等它结束再点。",
                    "job": snap,
                },
                409,
            )
            return
        self._json({"ok": True, "job": snap})

    def _compare(self, query: dict) -> None:
        """GET /api/compare?base=1&other=2 —— 逐流股 / 逐设备对比两个工况。"""
        try:
            base_id = int(query["base"][0])
            other_id = int(query["other"][0])
        except (KeyError, ValueError, IndexError):
            self._error("需要 base 和 other 两个工况 id,例如 /api/compare?base=1&other=2")
            return

        fields = [
            "temperature_c",
            "pressure_bar",
            "mass_flow_kgh",
            "mole_flow_kmolh",
            "vap_frac",
            "vol_flow_m3h",
        ]
        # 设备要比的字段:负荷/功率/级数/顶底温 + ΔH(焓平衡自检) + 泵的流量/扬程
        # ⚠️ 前端 app.js 的 BLOCK_COMPARE_FIELDS 必须同步,否则接口加了界面不显示
        block_fields = [
            "duty_kw",
            "power_kw",
            "nstage",
            "top_temp_c",
            "bottom_temp_c",
            "dh_kw",
            "ref_kw",
            "head_m",
            "flow_m3h",
        ]

        conn = db.open_db(self.db_path)
        try:
            base = db.get_case(conn, base_id)
            other = db.get_case(conn, other_id)
            if base is None or other is None:
                self._error("工况不存在", 404)
                return

            left = {r["name"]: row_to_dict(r) for r in db.read_streams(conn, base_id)}
            right = {r["name"]: row_to_dict(r) for r in db.read_streams(conn, other_id)}

            rows, common, changed = [], 0, 0
            for name in sorted(set(left) | set(right)):
                a, b = left.get(name), right.get(name)
                delta = {}
                for f in fields:
                    va = a.get(f) if a else None
                    vb = b.get(f) if b else None
                    delta[f] = (
                        round(vb - va, 6)
                        if isinstance(va, (int, float)) and isinstance(vb, (int, float))
                        else None
                    )
                if a and b:
                    common += 1
                    if any(d not in (None, 0) for d in delta.values()):
                        changed += 1
                rows.append(
                    {
                        "name": name,
                        "base": {f: a.get(f) for f in fields} if a else None,
                        "other": {f: b.get(f) for f in fields} if b else None,
                        "delta": delta,
                    }
                )

            # ---- 设备 ----
            b_left = {r["name"]: row_to_dict(r) for r in db.read_blocks(conn, base_id)}
            b_right = {r["name"]: row_to_dict(r) for r in db.read_blocks(conn, other_id)}
            block_rows, b_common, b_changed = [], 0, 0
            for name in sorted(set(b_left) | set(b_right)):
                a, b = b_left.get(name), b_right.get(name)
                delta = {}
                for f in block_fields:
                    va = a.get(f) if a else None
                    vb = b.get(f) if b else None
                    delta[f] = (
                        round(vb - va, 6)
                        if isinstance(va, (int, float)) and isinstance(vb, (int, float))
                        else None
                    )
                status_a = a.get("status") if a else None
                status_b = b.get("status") if b else None
                balance_a = a.get("balance_state") if a else None
                balance_b = b.get("balance_state") if b else None
                if a and b:
                    b_common += 1
                    if (
                        any(d not in (None, 0) for d in delta.values())
                        or status_a != status_b
                        or balance_a != balance_b
                    ):
                        b_changed += 1
                block_rows.append(
                    {
                        "name": name,
                        "type": (a or b or {}).get("type"),
                        "base": {f: a.get(f) for f in block_fields} if a else None,
                        "other": {f: b.get(f) for f in block_fields} if b else None,
                        "delta": delta,
                        "status": {"base": status_a, "other": status_b},
                        # 焓平衡判定不能相减,只能并排看:A 通过 → B 超差 才算"变了"
                        "balance": {"base": balance_a, "other": balance_b},
                    }
                )

            self._json(
                {
                    "base": {
                        "id": base["id"],
                        "model_name": base["model_name"],
                        "read_at": base["read_at"],
                        "severity": base["severity"],
                    },
                    "other": {
                        "id": other["id"],
                        "model_name": other["model_name"],
                        "read_at": other["read_at"],
                        "severity": other["severity"],
                    },
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
                }
            )
        finally:
            conn.close()

    def get_manual_docx(self, m, query):
        """GET /api/manuals/{pipe|pump}.docx —— 两册《计算说明书》的 Word 原件。

        管道页 / 泵页的「导出计算书」下发的就是这两份文件(不按工况现算):
        正文源是同名 `.md`,由 `tools/make_calc_manuals.py` 渲染成 docx;
        打包版靠 `tools/build_exe.py` 的 `--add-data` 带进 `_MEIPASS/docs`。
        """
        kind = (m.groupdict() or {}).get("kind")
        manual = MANUALS.get(str(kind or ""))
        if manual is None:
            self._error("没有这本计算书", 404)
            return
        path = paths.resource_root() / "docs" / manual["file"]
        try:
            data = path.read_bytes()
        except OSError:
            self._error(f"当前安装里没有 docs/{manual['file']},重装一次或拉一份仓库即可", 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", _DOCX)
        self.send_header(
            "Content-Disposition",
            _attachment(f"Casebook-{manual['title']}.docx", manual["ascii"]),
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def get_health(self, m, query):
        """GET /api/health —— 健康检查。"""
        self._json({"ok": True, "db": self.db_path})

    def get_version(self, m, query):
        """GET /api/version —— 本地版本号与仓库地址(不联网)。"""
        self._json(
            {
                "version": version.__version__,
                "releases_url": version.RELEASES_URL,
                "frozen": paths.is_frozen(),
            }
        )

    def get_read_status(self, m, query):
        """GET /api/read/status —— 读取进度/结果(前端每 0.5 s 问一次)。"""
        self._json(read_job.snapshot())

    def post_read(self, m, query):
        """POST /api/read —— 起一个后台读取任务。"""
        self._start_read()

    def post_shutdown(self, m, query):
        """POST /api/shutdown —— 停服务(换线程关,否则在请求线程里自己等自己)。"""
        import threading

        self._json({"ok": True, "message": "服务已停止"})
        threading.Timer(0.2, self.server.shutdown).start()

    def post_update_check(self, m, query):
        """POST /api/update/check —— 手动检查更新(唯一的出站请求)。"""
        from app.api import update_check

        self._json(update_check.check())

    def post_sample_install(self, m, query):
        """POST /api/sample/install —— 把内置示例库装进当前库(先备份原库)。"""
        from app.api.server import _install_sample

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

    def get_block_families(self, m, query):
        """GET /api/block-families —— 设备类型 → 家族(唯一来源在 design)。"""
        from app.design import block_family

        self._json(block_family.describe())

    def get_compare(self, m, query):
        """GET /api/compare —— 两个工况并排对比。"""
        self._compare(query)

    def post_case_sections(self, m, query):
        """POST /api/cases/{id}/sections —— 保存工段边界。"""
        self._save_sections(m["case_id"])

    def post_case_sections_inherit(self, m, query):
        """POST /api/cases/{id}/sections/inherit —— 沿用同模型更早工况的工段边界。"""
        self._inherit_sections(m["case_id"])

    def post_case_component_cas(self, m, query):
        """POST /api/cases/{id}/components/cas —— 手填组分 CAS(按模型存)。"""
        self._set_component_cas(m["case_id"])

    def get_case_sections(self, conn, case, m, query):
        """GET /api/cases/{id}/sections —— 工段边界:自动草稿 + 已确认 + 合并结果。"""
        self._sections(conn, int(m["case_id"]))

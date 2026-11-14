"""按设备分类导出 Excel —— 设备页右上角「导出 Excel」用。

为什么单独一个工作簿
--------------------
完整导出(`build_workbook`)是整个工况,点一次要等全表;设备页只需要**当前分类**
的设备明细。泵分类是把整本的「泵」页拿来用:流量 / 扬程 / 效率 / 轴功率 /
建议电机 / 进出口压力 / 压升 / 密度 / p_v / NPSHₐ —— 正好是选型要抄的那几列。

列与页面对齐
------------
* 泵以外的分类,列 = 设备页上该分类显示的列(与 `app/web/static/app.js` 的
  `FAMILY_COLS` 一致;改了那边记得改这里)。
* 数值按页面同样的位数取整,空值留空(不是 0)。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.design import block_family
from app.store import db

STATUS_TEXT = {0: "正常", 1: "错误", 2: "告警"}
BALANCE_TEXT = {"ok": "通过", "bad": "超差", "no_data": "无法校验", "skip": "不校验"}


def _r(value, digits: int):
    """按位数取整;None / 非数字一律留空。"""
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _num(key: str, digits: int):
    return lambda b: _r(b[key], digits)


# (表头, 列宽, 取值)
_COLS: dict[str, tuple[str, int, Callable[[Mapping[str, Any]], Any]]] = {
    "name": ("设备", 14, lambda b: b["name"]),
    "type": ("类型", 12, lambda b: b["type"]),
    "status": ("状态", 8, lambda b: STATUS_TEXT.get(b["status"], "")),
    "duty": ("热负荷 kW", 14, _num("duty_kw", 2)),
    "power": ("功率 kW", 12, _num("power_kw", 2)),
    "area": ("面积 m²", 14, _num("area_m2", 2)),
    "nstage": ("级数", 8, lambda b: b["nstage"]),
    "top": ("顶温 ℃", 12, _num("top_temp_c", 1)),
    "bottom": ("底温 ℃", 12, _num("bottom_temp_c", 1)),
    "dh": ("ΔH kW", 14, _num("dh_kw", 2)),
    "ref": ("参照 kW", 12, _num("ref_kw", 2)),
    "balance": ("焓平衡", 10, lambda b: BALANCE_TEXT.get(b["balance_state"], "")),
    "note": ("平衡说明", 46, lambda b: b["balance_note"]),
    "message": ("提示", 60, lambda b: b["message"]),
    "inT": ("进口 ℃", 10, _num("inlet_temp_c", 1)),
    "outT": ("出口 ℃", 10, _num("outlet_temp_c", 1)),
    "coldIn": ("冷侧进 ℃", 11, _num("cold_in_temp_c", 1)),
    "coldOut": ("冷侧出 ℃", 11, _num("cold_out_temp_c", 1)),
    "vapOut": ("出口汽化率", 11, _num("outlet_vap_frac", 3)),
    "cond": ("冷凝器 kW", 12, _num("cond_duty_kw", 2)),
    "reb": ("再沸器 kW", 12, _num("reb_duty_kw", 2)),
}

# 与 app.js 的 FAMILY_COLS 一致(顺序即列序)
FAMILY_COLUMNS: dict[str, tuple[str, ...]] = {
    "column": ("name", "status", "nstage", "top", "bottom", "cond", "reb", "message"),
    "heat": (
        "name",
        "type",
        "status",
        "duty",
        "area",
        "inT",
        "outT",
        "coldIn",
        "coldOut",
        "dh",
        "ref",
        "balance",
        "note",
        "message",
    ),
    "flash": (
        "name",
        "type",
        "status",
        "duty",
        "outT",
        "vapOut",
        "dh",
        "ref",
        "balance",
        "message",
    ),
    "react": ("name", "type", "status", "duty", "outT", "message"),
    "move": ("name", "type", "status", "power", "inT", "outT", "dh", "ref", "balance", "message"),
    "mix": ("name", "type", "status", "outT", "message"),
    "other": ("name", "type", "status", "message"),
}

# 泵:与整本导出的「泵」页一致(比页面多进出口压力/压升/密度,选型时要用)
PUMP_HEADERS = (
    "设备",
    "状态",
    "流量 m³/h",
    "扬程 m",
    "效率 %",
    "轴功率 kW",
    "建议电机 kW",
    "进口压力 bar",
    "出口压力 bar",
    "压升 bar",
    "密度 kg/m³",
    "进口汽化率",
    "p_v bar",
    "NPSHₐ m",
    "提示",
)
PUMP_WIDTHS = (14, 8, 12, 10, 10, 12, 14, 14, 14, 12, 12, 12, 10, 12, 50)


def build_family_workbook(conn, case_id: int, family: str) -> tuple[bytes, str, str]:
    """生成单分类设备表。返回 (xlsx 字节, 分类显示名, 文件名)。"""
    import io

    import openpyxl
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    if family not in FAMILY_COLUMNS and family != "pump":
        raise ValueError(f"未知分类:{family}")

    case = db.get_case(conn, case_id)
    if case is None:
        raise LookupError(case_id)

    label = block_family.FAMILY_LABELS.get(family, family)
    blocks = [
        b for b in db.read_blocks(conn, case_id) if block_family.family_of(b["type"]) == family
    ]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = label

    if family == "pump":
        from app.design import pump as pump_rules

        def npsh(b):
            return _r(pump_rules.npsh_available_m(b["p_in_bar"], b["pv_bar"], b["rho_kgm3"]), 2)

        headers = list(PUMP_HEADERS)
        widths = list(PUMP_WIDTHS)
        rows = [
            [
                b["name"],
                STATUS_TEXT.get(b["status"], ""),
                _r(b["flow_m3h"], 2),
                _r(b["head_m"], 2),
                None if b["pump_eff"] is None else _r(b["pump_eff"] * 100, 1),
                _r(b["power_kw"], 2),
                b["motor_kw"],
                b["p_in_bar"],
                b["p_out_bar"],
                b["dp_bar"],
                _r(b["rho_kgm3"], 1),
                _r(b["pump_vap_frac"], 4),
                _r(b["pv_bar"], 4),
                npsh(b),
                b["message"],
            ]
            for b in blocks
        ]
    else:
        cols = FAMILY_COLUMNS[family]
        headers = [_COLS[c][0] for c in cols]
        widths = [_COLS[c][1] for c in cols]
        rows = [[_COLS[c][2](b) for c in cols] for b in blocks]

    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
    for i, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width

    buf = io.BytesIO()
    wb.save(buf)
    stamp = case["read_at"].replace("-", "").replace(":", "").replace(" ", "_")[:13]
    fname = f"Casebook_case{case_id}_{family}_{stamp}.xlsx"
    return buf.getvalue(), label, fname

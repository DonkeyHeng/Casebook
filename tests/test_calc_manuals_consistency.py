"""计算说明书与 app/design 常量、交叉引用的一致性回归。

把「靠人眼和记忆防回归」变成 pytest：说明书里的核心常数、默认参数、
两册共同阈值和一个交叉引用死链都能在这里当场报错。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.design.pipe.constants import ATM_MPA as P_ATM
from app.design.pipe.data import (
    HP_SS_CORROSION_MM,
    HP_SS_GRADES,
    HP_SS_MIN_WALL_MM,
    HP_SS_NEG_DEV_FACTOR,
    LOW_TEMP_NOTE_C,
    MIN_WALL_BY_DN,
    PN_CODES,
    STREAM_NO_PATTERN,
    WELDED_WALL_SERIES,
)
from app.design.pump_line_context import default_pipe_options
from app.values import (
    MOTOR_EFF,
    MOTOR_KW,
    MOTOR_MARGIN_TIERS,
    TRANSMISSION_EFF_DIRECT,
    TWO_PHASE_VAP_EPS,
)
from tools.check_manual_content import check_all
from tools.check_manual_content import selftest as check_content_selftest
from tools.check_manual_refs import main as check_manual_refs

ROOT = Path(__file__).resolve().parents[1]
DOCS = {
    "pipe": ROOT / "docs" / "casebook-pipe-calc-manual.md",
    "pump": ROOT / "docs" / "casebook-pump-calc-manual.md",
}
SOURCE = ROOT / "tools" / "make_calc_manuals.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _norm(text: str) -> str:
    """统一排版差异：Unicode 减号、全角空格与不换行空格。"""
    return text.replace("\u2212", "-").replace("\u3000", " ").replace("\xa0", " ")


def _sci(value: float) -> str:
    """1e-4 这类说明书里常用的科学记数写法。"""
    return f"{value:.0e}".replace("e-0", "e-")


def _motor_kw(value: float) -> str:
    """电机档位的展示格式：1 kW 以下两位、1–10 kW 一位、以上按需保留小数。"""
    if value < 1:
        return f"{value:.2f}"
    if value < 10:
        return f"{value:.1f}"
    return f"{value:g}"


def test_calc_manuals_consistency() -> None:
    pipe = _norm(_read(DOCS["pipe"]))
    pump = _norm(_read(DOCS["pump"]))

    # 1. 相态容差：两册共用同一个常量，不能一册写 1e-4、另一册写 0.0001。
    eps = _sci(TWO_PHASE_VAP_EPS)
    assert eps in pipe
    assert eps in pump

    # 2. 管路册核心常数与默认值。
    assert f"{P_ATM:g} MPa" in pipe
    assert f"{HP_SS_MIN_WALL_MM:.1f} mm" in pipe
    assert f"{HP_SS_NEG_DEV_FACTOR:g}" in pipe
    assert f"{HP_SS_CORROSION_MM:.1f} mm" in pipe
    assert all(grade in pipe for grade in HP_SS_GRADES)
    assert f"≤ {LOW_TEMP_NOTE_C:g} ℃" in pipe
    assert "/".join(f"{value:.1f}" for value in MIN_WALL_BY_DN.values()) in pipe
    assert f"{PN_CODES['U']:.1f} MPa" in pipe

    wall_series = " / ".join(f"{value:g}" for value in WELDED_WALL_SERIES) + " mm"
    assert wall_series in pipe

    # 正则正文里写成转义形式：Markdown 保留一份反斜杠，生成器源码写成两份。
    escaped_pattern = STREAM_NO_PATTERN.replace("\\", "\\\\")
    assert STREAM_NO_PATTERN in pipe
    assert escaped_pattern not in pipe  # md 是正文源，不应残留源码级转义

    # 3. 泵册电机档位、分段裕量、传动与电功率折算。
    assert "、".join(_motor_kw(value) for value in MOTOR_KW) in pump
    for margin in (tier[1] for tier in MOTOR_MARGIN_TIERS):
        assert f"{margin:.2f}" in pump
    assert f"{MOTOR_EFF:g}" in pump
    assert f"{TRANSMISSION_EFF_DIRECT:.1f}" in pump
    assert "界面路径固定直联" in pump

    # 4. 两册公共阈值与管道页默认参数必须逐字一致。
    options = default_pipe_options()
    velocity = options["velocity"]
    defaults = (
        f"流速 {velocity['liquid']:g} / {velocity['gas']:g} / "
        f"{velocity['two']:g} / {velocity['pump_inlet']:g} m/s、"
        f"设计压力倍率 {options['pressure_factor']:g}、"
        f"附加 {options['pressure_add_mpa']:g} MPa、"
        f"下限 {options['pressure_floor_mpa']:.1f} MPa、"
        f"最大 DN{options['max_dn']}、默认材质 {options['material']}"
    )
    assert defaults in pump
    assert (
        f"{options['pressure_factor']:g} / {options['pressure_add_mpa']:g} MPa / "
        f"{options['pressure_floor_mpa']:.1f} MPa"
    ) in pipe
    assert f"| max_dn | {options['max_dn']} |" in pipe

    shared = {
        "相态容差": eps,
        "R1E 负偏差": "12.5%",
        "自定义规格语义": "C1=0",
        "Word 报告出口": "Word 报告",
    }
    for label, value in shared.items():
        assert value in pipe, f"管路册缺少{label}：{value}"
        assert value in pump, f"泵册缺少{label}：{value}"

    # 5. 旧口径残留。修订记录会引用旧表述，只检查正文与代码映射附录。
    pipe_body = pipe.split("## 附录 B", 1)[0]
    pump_body = pump.split("## 附录 B", 1)[0]
    for stale in (
        "超温才早退",
        "只扣 C1 一次",
        "改用吸入流股流量",
        "按 TOTAL → MIXED → LIQUID 逐层下钻",
        "DN>600",
        "22.0 MPa 时 grade_code",
    ):
        assert stale not in pipe_body, f"管路册残留旧口径：{stale}"
        assert stale not in pump_body, f"泵册残留旧口径：{stale}"
    assert not re.search(r"(?<!Word )报告中的泵段", pump_body)

    # 6. 复用仓库自己的引用扫描器，任何 见 X.X 节 / 表 X-X 死链都失败。
    assert check_manual_refs() == 0

    # 7. 内容级自检：条文描述的代码行为（路径层数、单位系数、标识符、R1E 边界、
    #    旧口径、docx 载体）逐条比对源码。先证明规则本身有效，再用它查文档——
    #    否则规则写成永远通过的样子，绿灯仍然是假绿灯。
    assert not check_content_selftest(), "检查器规则没生效（变异样本未报错）"
    content = check_all()
    assert not content, "说明书内容级自检失败：\n" + "\n".join(item.render() for item in content)

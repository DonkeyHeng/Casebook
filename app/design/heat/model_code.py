"""GB/T 151-2014 换热器型号编码器。

格式:`<前端><壳体><后端><DN>-<Pt>/<Ps>-<A>-<L>/<d>-<N><管束级别>`

铁律(方案 §5.11):

1. **缺任一段不输出完整 code**,只给 `code_partial`(缺项用 `?` 占位)与 `missing[]`;
2. 压力段按 **MPa** 写入(`bar / 10`),保留 3 位小数并去掉多余 0;
3. 面积段必须用**公称面积** `area_nominal_m2`,不是需求面积 `A_req`;
4. 前端/壳体/后端/管程数/级别只认标准枚举,非法值直接报错,不静默纠正。

第一版覆盖:管壳式(BEM 为主,兼容 AES / BKU 等常见组合编码规则);
板式、空冷器不在编码范围内(层内由调用方拦掉,本模块不猜)。
"""

from __future__ import annotations

FRONT_HEADS = {
    "A": "平盖",
    "B": "椭圆封头(可拆)",
    "C": "椭圆封头(不可拆)",
    "D": "特殊高压",
}
SHELL_TYPES = {
    "E": "单进单出",
    "F": "双进单出",
    "G": "分流",
    "H": "双分流",
    "J": "分流壳体",
    "K": "釜式",
    "X": "特殊",
}
REAR_HEADS = {
    "M": "固定管板(带管箱)",
    "N": "固定管板(无管箱)",
    "S": "浮头",
    "T": "填料函",
    "U": "U 形管",
    "W": "带套环填料函",
}
PASS_COUNTS = (1, 2, 4, 6)
BUNDLE_CLASSES = ("I", "II")

# 本工具第一版实际支持的组合(其余能编码但不建议,界面提示"超出首选范围")
PREFERRED_REAR = ("M", "S", "U")


def bar_to_mpa(bar: float | None) -> float | None:
    """bar → MPa,保留 3 位小数(编码用;不在展示层用)。"""
    if bar is None:
        return None
    return round(float(bar) / 10.0, 3)


def format_mpa(bar: float | None) -> str | None:
    """bar → 编码用 MPa 文本:最多 3 位小数,去掉末尾 0。

    例:5.5 bar → '0.55';16 bar → '1.6';0.6 bar → '0.06'。
    """
    mpa = bar_to_mpa(bar)
    if mpa is None:
        return None
    text = f"{mpa:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def build_code(
    front: str | None = None,
    shell: str | None = None,
    rear: str | None = None,
    dn_mm: float | int | None = None,
    p_tube_bar: float | None = None,
    p_shell_bar: float | None = None,
    area_nominal_m2: float | None = None,
    tube_length_m: float | None = None,
    tube_od_mm: float | None = None,
    passes: int | None = None,
    bundle_class: str = "I",
) -> dict:
    """生成型号编码。

    返回:
        code        完整编码或 None
        code_partial 缺项用 `?` 的占位串(永远有值)
        missing     缺项的中文名列表(顺序与编码段一致)
        errors      非法枚举/数值的说明(非空时 code 一定为 None)
        segments    已填段的结构化字典(便于界面逐段显示)
    """
    errors: list[str] = []
    missing: list[str] = []
    segments: dict[str, str | None] = {}

    front_u = (front or "").upper() or None
    shell_u = (shell or "").upper() or None
    rear_u = (rear or "").upper() or None
    if front_u and front_u not in FRONT_HEADS:
        errors.append(f"前端代码 {front!r} 不在 GB/T 151 枚举(A/B/C/D)")
    if shell_u and shell_u not in SHELL_TYPES:
        errors.append(f"壳体代码 {shell!r} 不在 GB/T 151 枚举(E/F/G/H/J/K/X)")
    if rear_u and rear_u not in REAR_HEADS:
        errors.append(f"后端代码 {rear!r} 不在 GB/T 151 枚举(M/N/S/T/U/W)")

    segments["front"] = front_u
    segments["shell"] = shell_u
    segments["rear"] = rear_u
    if not front_u:
        missing.append("前端")
    if not shell_u:
        missing.append("壳体")
    if not rear_u:
        missing.append("后端")

    dn = int(dn_mm) if isinstance(dn_mm, (int, float)) and dn_mm > 0 else None
    if dn is None:
        missing.append("公称直径 DN")
    segments["dn_mm"] = str(dn) if dn else None

    pt = format_mpa(p_tube_bar) if p_tube_bar is not None and p_tube_bar > 0 else None
    ps = format_mpa(p_shell_bar) if p_shell_bar is not None and p_shell_bar > 0 else None
    if pt is None:
        missing.append("管程设计压力 Pt")
    if ps is None:
        missing.append("壳程设计压力 Ps")
    segments["p_tube_mpa"] = pt
    segments["p_shell_mpa"] = ps

    area = (
        float(area_nominal_m2)
        if isinstance(area_nominal_m2, (int, float)) and area_nominal_m2 > 0
        else None
    )
    if area is None:
        missing.append("公称换热面积 A")
    segments["area_m2"] = f"{area:g}" if area else None

    length = (
        float(tube_length_m)
        if isinstance(tube_length_m, (int, float)) and tube_length_m > 0
        else None
    )
    od = float(tube_od_mm) if isinstance(tube_od_mm, (int, float)) and tube_od_mm > 0 else None
    if length is None:
        missing.append("公称管长 L")
    if od is None:
        missing.append("换热管外径 d")
    segments["tube_length_m"] = f"{length:g}" if length else None
    segments["tube_od_mm"] = f"{od:g}" if od else None

    n = int(passes) if isinstance(passes, int) and passes in PASS_COUNTS else None
    if n is None:
        if passes is None:
            missing.append("管程数 N")
        else:
            errors.append(f"管程数 {passes!r} 不在允许值 {PASS_COUNTS}")
    segments["passes"] = str(n) if n else None

    cls = (bundle_class or "").upper() or None
    if cls and cls not in BUNDLE_CLASSES:
        errors.append(f"管束级别 {bundle_class!r} 只能是 I / II")
        cls = None
    if not cls:
        missing.append("管束级别")
    segments["bundle_class"] = cls

    body_left = f"{front_u or '?'}{shell_u or '?'}{rear_u or '?'}"
    body_mid = (
        f"{dn if dn else '?'}-"
        f"{pt or '?'}/{ps or '?'}-"
        f"{f'{area:g}' if area else '?'}-"
        f"{f'{length:g}' if length else '?'}/{f'{od:g}' if od else '?'}-"
        f"{n if n else '?'}{cls or '?'}"
    )
    partial = f"{body_left}{body_mid}"

    code = None
    if not errors and not missing:
        code = partial

    notes: list[str] = []
    if rear_u and rear_u not in PREFERRED_REAR:
        notes.append(f"后端 {rear_u} 超出第一版首选(M/S/U),编码保留但界面应提示覆盖边界")

    return {
        "code": code,
        "code_partial": partial,
        "missing": missing,
        "errors": errors,
        "segments": segments,
        "notes": notes,
    }


def explain(code_result: dict) -> str:
    """把编码结果写成人话,给计算说明书与界面提示用。"""
    if code_result.get("errors"):
        return "编码未生成:" + ";".join(code_result["errors"])
    if code_result.get("missing"):
        return "编码缺项(" + "、".join(code_result["missing"]) + "),已输出占位串,未冒充完整型号"
    return f"型号 {code_result['code']}"

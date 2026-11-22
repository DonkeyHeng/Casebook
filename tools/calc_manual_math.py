"""把说明书里的公式与带上下标的符号渲染成 Word 原生公式（OMML）。

md 正文仍保持纯文本写法，本模块只负责“怎么显示”：
- 正式公式：按完整文本匹配到一组 LaTeX，交给 pandoc 生成 OMML；
- 正文符号：按白名单逐词替换，生成行内 OMML；
- 表格与代码块不进入这里，避免 9–13 列宽表被公式撑高、代码标识符被误认成变量。
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

_M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _q(tag: str) -> str:
    return f"{{{_M_NS}}}{tag}"


# 正式公式：键与 md 中的整行一致；值是 (LaTeX, 公式下方的说明文字)。
FORMULAS: dict[str, list[tuple[str, str]]] = {
    "v = min(v₀, 0.8) （近沸点液体且非泵入口、非泵出口、非升压线）": [
        (r"v=\text{min}\left(v_0,\ 0.8\right)", "近沸点液体且非泵入口、非泵出口、非升压线"),
    ],
    "Di = 1000 · √( 4·(Q/3600) / (π·v) ) [mm]": [
        (
            r"D_i=1000\sqrt{\frac{4\left(Q/3600\right)}{\pi v}}\quad\left[\text{mm}\right]",
            "",
        )
    ],
    "ρm = m / Q ； v_lim = √(2000 / ρm)": [
        (
            r"\rho_m=\frac{m}{Q},\qquad v_{\text{lim}}=\sqrt{\frac{2000}{\rho_m}}",
            "",
        )
    ],
    "Di = max( Di, 1000·√(4·(Q/3600)/(π·v_lim)) )": [
        (
            r"D_i=\text{max}\left(D_i,\ 1000\sqrt{\frac{4\left(Q/3600\right)}"
            r"{\pi v_{\text{lim}}}}\right)",
            "",
        )
    ],
    "P_表 = P_绝(MPa) − P_atm ； Pd = max(0, K_p·P_表, P_表 + ΔP_add, P_floor) [MPa(G)]": [
        (
            r"P_{\text{表}}=P_{\text{绝}}\left(\text{MPa}\right)-P_{\text{atm}}\quad\left[\text{MPa}\right]",
            "",
        ),
        (
            r"P_d=\text{max}\left(0,\ K_pP_{\text{表}},\ P_{\text{表}}+\Delta P_{\text{add}},"
            r"\ P_{\text{floor}}\right)"
            r"\quad\left[\text{MPa(G)}\right]",
            "",
        ),
    ],
    "P_绝 < 95 kPa → Pd = 0.1 MPa，真空代号 L，is_vacuum = True": [
        (
            r"P_{\text{绝}}<95\ \text{kPa}\ \Rightarrow\ P_d=0.1\ \text{MPa}",
            "真空代号 L，is_vacuum = True",
        )
    ],
    "Td = t_操作 + ΔT_m（t ≥ 0）； Td = t_操作 − ΔT_m（t < 0）": [
        (
            r"T_d=t_{\text{操作}}+\Delta T_m\ \ (t\ge 0),\qquad "
            r"T_d=t_{\text{操作}}-\Delta T_m\ \ (t<0)",
            "",
        )
    ],
    "tc = P·Do / [ 2·(σ·E_j + P·Y) ]": [
        (r"t_c=\frac{P D_o}{2\left(\sigma E_j+P Y\right)}", ""),
    ],
    "tc = Pd·Do / (2·σ·E_j + Pd) ； t = tc + C ； treq,nom = t / 0.875": [
        (r"t_c=\frac{P_d D_o}{2\sigma E_j+P_d}", ""),
        (r"t=t_c+C", ""),
        (r"t_{\text{req,nom}}=\frac{t}{0.875}", ""),
    ],
    "t_ext = Do · ( SF·P_ext / (2E/(1−ν²)) )^(1/3)": [
        (
            r"t_{\text{ext}}=D_o\left(\frac{S_F P_{\text{ext}}}{2E/\left(1-\nu^2\right)}"
            r"\right)^{1/3}",
            "",
        )
    ],
    "t_ext_req = t_ext + C2 ； treq = max( treq, t_ext_req ) ； S − C1 ≥ treq": [
        (r"t_{\text{ext,req}}=t_{\text{ext}}+C_2", ""),
        (r"t_{\text{req}}=\text{max}\left(t_{\text{req}},\ t_{\text{ext,req}}\right)", ""),
        (r"S-C_1\ge t_{\text{req}}", ""),
    ],
    "NPSHₐ(进口基准) = (p_进口 − p_v) × 10⁵ / (ρ · g) ， g = 9.80665 m/s²": [
        (
            r"\text{NPSH}_a(\text{进口基准})="
            r"\frac{\left(p_{\text{进口}}-p_v\right)\times 10^5}{\rho g},\qquad "
            r"g=9.80665\ \text{m/s}^2",
            "",
        )
    ],
    "NPSHₐ(装置有效) = NPSHₐ(进口基准) − ΔZ_吸入 − Σhf_吸入": [
        (
            r"\text{NPSH}_a(\text{装置有效})="
            r"\text{NPSH}_a(\text{进口基准})-\Delta Z_{\text{吸入}}-\Sigma h_{f,\text{吸入}}",
            "",
        )
    ],
    "H = Hpr + (ΔZ_吸入 + ΔZ_排出) + (Σhf_吸入 + Σhf_排出)": [
        (
            r"H=H_{\text{pr}}+\left(\Delta Z_{\text{吸入}}+\Delta Z_{\text{排出}}\right)"
            r"+\left(\Sigma h_{f,\text{吸入}}+\Sigma h_{f,\text{排出}}\right)",
            "",
        )
    ],
    "H_选型 = ceil(1.1 × H)": [
        (r"H_{\text{选型}}=\left\lceil 1.1H\right\rceil", ""),
    ],
    "d = Do − 2·S [mm] ； d_m = d / 1000 [m] ； u = (Q/3600) / (π·d_m²/4)": [
        (r"d=D_o-2S\quad\left[\text{mm}\right]", ""),
        (r"d_m=\frac{d}{1000}\ \ [\text{m}]", ""),
        (r"u=\frac{Q/3600}{\pi d_m^2/4}", ""),
    ],
    "Σhf = ( λ·L/d_m + Σζ ) · u² / (2g) ， g = 9.80665 m/s²": [
        (
            r"\Sigma h_f=\left(\frac{\lambda L}{d_m}+\Sigma\zeta\right)\frac{u^2}{2g},"
            r"\qquad g=9.80665\ \text{m/s}^2",
            "",
        )
    ],
    "Re = d_m·u·ρ / (μ/1000)": [
        (r"\text{Re}=\frac{d_m u\rho}{\mu/1000}", ""),
    ],
    "P_need = P_基准 / η_传动 × m(P_基准) ； 建议电机 = "
    "档位表中第一个 ≥ P_need 的值（m 为按基准功率取的分段裕量）": [
        (
            r"P_{\text{need}}=\frac{P_{\text{基准}}}{\eta_{\text{传动}}}\times"
            r" m\left(P_{\text{基准}}\right)",
            "建议电机取档位表中第一个 ≥ P_need 的值；m 为按基准功率取的分段裕量",
        )
    ],
    "P_估 = ρ·Q·H·g ÷ (3.6×10⁶·η)": [
        (r"P_{\text{估}}=\frac{\rho Q H g}{3.6\times 10^6\eta}", ""),
    ],
}


# 正文行内公式：只收真正的变量/符号，字段名（min_wall_mm 等）保持代码写法。
INLINE_MATH: dict[str, str] = {
    "NPSHₐ": r"\text{NPSH}_a",
    "Σhf_吸入": r"\Sigma h_{f,\text{吸入}}",
    "Σhf_排出": r"\Sigma h_{f,\text{排出}}",
    "Σhf": r"\Sigma h_f",
    "Σζ": r"\Sigma\zeta",
    "ΔZ_吸入": r"\Delta Z_{\text{吸入}}",
    "ΔZ_排出": r"\Delta Z_{\text{排出}}",
    "ΔP_add": r"\Delta P_{\text{add}}",
    "ΔT_m": r"\Delta T_m",
    "P_表": r"P_{\text{表}}",
    "P_绝": r"P_{\text{绝}}",
    "P_atm": r"P_{\text{atm}}",
    "P_floor": r"P_{\text{floor}}",
    "P_need": r"P_{\text{need}}",
    "P_基准": r"P_{\text{基准}}",
    "P_估": r"P_{\text{估}}",
    "K_p": r"K_p",
    "E_j": r"E_j",
    "t_操作": r"t_{\text{操作}}",
    "t_ext_req": r"t_{\text{ext,req}}",
    "t_ext": r"t_{\text{ext}}",
    "t_req,nom": r"t_{\text{req,nom}}",
    "treq,nom": r"t_{\text{req,nom}}",
    "v_lim": r"v_{\text{lim}}",
    "d_m": r"d_m",
    "ρm": r"\rho_m",
    "ρv²": r"\rho v^2",
    "[σ]^t": r"[\sigma]^t",
    "H_pr": r"H_{\text{pr}}",
    "Hpr": r"H_{\text{pr}}",
    "H_选型": r"H_{\text{选型}}",
    "H_sel": r"H_{\text{sel}}",
    "C1": r"C_1",
    "C2": r"C_2",
    "D_i": r"D_i",
    "D_o": r"D_o",
    "P_d": r"P_d",
    "T_d": r"T_d",
    "t_c": r"t_c",
    "t_req": r"t_{\text{req}}",
    "Di": r"D_i",
    "Do": r"D_o",
    "Pd": r"P_d",
    "Td": r"T_d",
    "tc": r"t_c",
}

_INLINE_RE = re.compile(
    r"(?<![A-Za-z0-9_])("
    + "|".join(re.escape(key) for key in sorted(INLINE_MATH, key=len, reverse=True))
    + r")(?![A-Za-z0-9_])"
)


def _run_props(run, rpr):
    fonts = OxmlElement("w:rFonts")
    fonts.set(qn("w:ascii"), "Cambria Math")
    fonts.set(qn("w:hAnsi"), "Cambria Math")
    fonts.set(qn("w:eastAsia"), "宋体")
    rpr.append(fonts)
    size = OxmlElement("w:sz")
    size.set(qn("w:val"), str(max(int(round(run * 2)), 1)))
    rpr.append(size)
    size_cs = OxmlElement("w:szCs")
    size_cs.set(qn("w:val"), str(max(int(round(run * 2)), 1)))
    rpr.append(size_cs)


def _style_math(node, font_pt: float):
    for r in node.iter(_q("r")):
        if r.find(qn("w:rPr")) is not None:
            continue
        rpr = OxmlElement("w:rPr")
        _run_props(font_pt, rpr)
        text = r.find(_q("t"))
        if text is None:
            r.append(rpr)
        else:
            text.addprevious(rpr)


@lru_cache(maxsize=256)
def _omml_xml(latex: str, display: bool) -> bytes | None:
    """调 pandoc 生成一份临时 docx，抽出 OMML。失败返回 None，由调用方回退纯文本。"""
    markdown = f"$${latex}$$" if display else f"${latex}$"
    with tempfile.TemporaryDirectory(prefix="calc-math-") as tmp:
        out = Path(tmp) / "math.docx"
        try:
            subprocess.run(
                ["pandoc", "-f", "markdown", "-t", "docx", "-o", str(out)],
                input=markdown,
                text=True,
                check=True,
                capture_output=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        from zipfile import ZipFile

        with ZipFile(out) as archive:
            root = etree.fromstring(archive.read("word/document.xml"))
    tag = _q("oMathPara") if display else _q("oMath")
    nodes = root.findall(f".//{tag}")
    if not nodes:
        return None
    return etree.tostring(nodes[0])


def add_math(paragraph, latex: str, font_pt: float, display: bool = False) -> bool:
    xml = _omml_xml(latex, display)
    if xml is None:
        return False
    node = etree.fromstring(xml)
    _style_math(node, font_pt)
    paragraph._p.append(node)
    return True


def formula_parts(text: str) -> list[tuple[str, str]] | None:
    """按整行匹配正式公式；未命中返回 None，不擅自把说明文字当公式。"""
    normalized = re.sub(r"\s*\n\s*", " ", text).strip()
    return FORMULAS.get(normalized)


def has_inline_math(text: str) -> bool:
    return bool(_INLINE_RE.search(text))


def inline_matches(text: str):
    return _INLINE_RE.finditer(text)


def inline_latex(token: str) -> str:
    return INLINE_MATH[token]

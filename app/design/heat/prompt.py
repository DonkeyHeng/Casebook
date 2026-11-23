"""「复制填写提示词」正文 —— 换热器标准表与 EDR 送审两处出口的唯一来源。

三个出口同源(不要各抄一份):
  1. 页面「复制填写提示词」→ `GET /api/hx-standards-prompt` → 剪贴板;
  2. `samples/hx-standards-template/` 的两份 CSV(表头与示例行);
  3. 本模块 `standards_prompt()`,正文里原样嵌入模板的表头与示例行。

为什么用函数拼:文件名、必需列、最少行数全由 `standards.STD_FILES` 与校验逻辑
决定,改一处提示词自动跟着变 —— 手写提示词必然与校验漂移,漂移的后果是
用户照着提示填了却被拒。

口径边界:本模块只产出文字,不含任何真实标准数值;示例行是虚构的。
"""

from __future__ import annotations

PROMPT_VERSION = "2026-11-23"

_INTRO = """# 角色与任务
你是一位熟悉 GB/T 28712.2《热交换器型式与基本参数 第 2 部分:固定管板式热交换器》
与 GB/T 151 的换热器工艺工程师。请把我提供的标准表格(截图 / PDF 文字 / 手工抄录)
整理成 2 个 CSV 文件,供 Casebook 的换热器选型模块(L2 几何初选)直接导入。

三条硬约束:
1. 只转录,不改写:数值照抄原文,不四舍五入、不换算单位、不用「典型值」补空。
2. 读不到的格子留空,并在末尾「缺口清单」逐条列出,不要猜。
3. 不要输出标准原文之外的数据;不确定的行宁可不写。"""

_RULES = """# 格式硬要求
- UTF-8 编码(带 BOM 更好);一行表头;逗号分隔(Excel 另存为「CSV UTF-8」即可)。
- 不要合并单元格、不要多层表头、不要小计行、不要在表头前后加说明文字。
- 长度一律填 **mm**,面积填 **m²**,公称压力填 **bar**。
  标准原文的公称压力是 MPa:请乘 10 换成 bar 再填(0.25 MPa → 2.5 bar,6.40 MPa → 64 bar)。
- `area_m2` 抄标准表里「计算换热面积(A1)」那一列,**不要**自己用 π·d·L·n 重算;
  标准按 A1 = n·π·d_o·(L − 2δ − 2L1) 计算(δ 为管板厚、L1 为伸出长度),两者差 1%~2% 是正常的。
- `arrangement` 只填 `triangular` / `rotated_triangular` / `square` / `rotated_square` 之一;
  正三角形填 `triangular`,转角正三角形填 `rotated_triangular`。
- `tube_passes` 只填 1 / 2 / 4 / 6。标准表里没出现的组合不要编。
- `standard` 列写实际版本,如 `GB/T 28712.2-2023`;**2023 版与 2012 版数值不同,不要混用**。
- `page` 列写页码或表号(如 `表5 P6`),便于以后核对。
- 一个格子一个数:不要写 `19×2`,要拆成 `tube_od_mm=19`、`tube_wall_mm=2`。"""

_OUTRO = """# 输出方式
按文件逐个输出,每个文件前单独写一行标题(写文件名与来源,不要写进 CSV 本体),
然后是代码块里的完整 CSV。两个文件都给,不要省略表头。

# 交付前自检
- [ ] 文件名严格是 hx-28712-2.csv / hx-pass-combinations.csv
- [ ] 系列表有效行 ≥ 20,至少 3 档 DN、至少 2 种管外径(19 与 25 各一张表)
- [ ] 面积一列抄的是「计算换热面积 A1」,不是自己算的 πdL
- [ ] 管心距在 1.20~1.70 倍管外径之间(φ19 通常 25,φ25 通常 32)
- [ ] 没有空表头、没有单位混进数值列、没有把 MPa 当 bar 填
- [ ] 末尾附「缺口清单」:哪些页/哪些行没读到、为什么"""

_FALLBACK_HEADERS = {
    "hx-28712-2.csv": [
        "DN",
        "shell_id_mm",
        "tube_od_mm",
        "tube_wall_mm",
        "tube_length_mm",
        "tube_pitch_mm",
        "tube_passes",
        "tube_count",
        "area_m2",
        "arrangement",
    ],
    "hx-pass-combinations.csv": ["shell_passes", "tube_passes", "need_f_correction", "allowed"],
}


def _example_blocks() -> str:
    """两份表的文件清单 + 表头 + 示例行(示例行直接取模板 CSV)。"""
    from . import standards

    preview = standards.template_preview()
    by_name = {f["name"]: f for f in preview.get("files", [])}
    blocks: list[str] = []
    for spec in standards.STD_FILES:
        name = spec["name"]
        item = by_name.get(name) or {}
        headers = item.get("headers") or _FALLBACK_HEADERS.get(name) or list(spec["columns"])
        rows = item.get("rows") or []
        body = "\n".join(",".join(str(cell) for cell in row) for row in rows)
        lines = [
            f"### {name}",
            f"来源:{spec['from']};必需列:{'、'.join(spec['columns'])};最少 {spec['need']} 行数据",
            ",".join(headers),
        ]
        if body:
            lines.append(body)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def standards_prompt() -> str:
    """标准表填写提示词(每次现拼,模板改了跟着改)。"""
    from . import standards

    file_lines = [
        f"- `{spec['name']}` —— {spec['from']},必需列 "
        + "、".join(f"`{c}`" for c in spec["columns"])
        + f",至少 {spec['need']} 行"
        for spec in standards.STD_FILES
    ]
    return "\n\n".join(
        [
            _INTRO,
            "# 交付文件(文件名一字不改,导入时按名字对号入座)\n" + "\n".join(file_lines),
            _RULES,
            "# 格式示例(数值为虚构,只示列名与写法,不要照抄这些数)\n\n" + _example_blocks(),
            _OUTRO,
        ]
    )


_EDR_INTRO = """# 任务
把下面这张「EDR 输入参数清单」交给 Aspen EDR(或同类管壳式换热器 rating 软件)复核。
Casebook 只做到**简捷级估计**(LMTD + 经验 U + Kern 口径压降),不代替 rating:

- 膜系数用 Dittus-Boelter / Kern,偏差可达 2~3 倍;
- 未做振动分析、未做两相分区、未做机械强度;
- 判定里出现「参考」或「未取得」的项,都必须由 EDR 重新计算。"""

_EDR_OUTRO = """# 请对方确认的三件事
1. 面积裕量是否够(本工具给的 A_req 与标准公称面积 A_std 的关系);
2. 管程/壳程压降是否在允许值内(允许压降按 EDR 惯例 10%/20% 估);
3. 是否存在振动、死区、两相分区问题(本工具完全没有覆盖)。"""


def edr_prompt() -> str:
    """EDR 送审提示词(与 `heat-edr.xlsx` 的「结果与提示」页配套)。"""
    return "\n\n".join(
        [
            _EDR_INTRO,
            "# 随附文件\n- `heat-edr.xlsx`:三个页签(EDR工艺输入 / EDR结构输入 / 结果与提示)\n"
            "- 空值 = 未取得,不是 0;「数据来源」列写明 Aspen 实测 / 人工 / 预设 / 估算",
            _EDR_OUTRO,
        ]
    )

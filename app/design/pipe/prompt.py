"""给「导入标准表」页面用的填写提示词 —— 唯一来源。

三个出口都读这里,不要各抄一份:
  1. 页面「复制填写提示词」按钮 → `GET /api/pipe-standards-prompt` → 剪贴板;
  2. `samples/pipe-standards-template/` 里的三份 CSV(表头与示例行同源);
  3. 本模块的 `build()` 会在正文里原样嵌入那三份模板的表头与示例行。

为什么用函数拼而不是写死一段字
------------------------------
文件名、必需列、最少行数都由 `pipe_data.STD_FILES` 决定,示例行直接读模板 CSV。
以后加一张标准表或改列名,只要改 `STD_FILES` 与模板文件,提示词自动跟着变 ——
手写的提示词必然与校验逻辑漂移,而漂移的后果是用户按提示填了却被拒。

口径边界
--------
* 本模块只产出**文字**,不生成任何数值;示例行是虚构的,已在正文里写明。
* 标准原文受著作权保护,提示词不包含任何真实标准数据。
"""

from __future__ import annotations

PROMPT_VERSION = "2026-11-18"

_INTRO = """# 角色与任务
你是一位熟悉 GB/T 17395、GB/T 20801.1 与 HG/T 20519 的压力管道材料工程师。
请把我提供的标准表格(截图 / PDF 文字 / 手工抄录)整理成 3 个 CSV 文件,供 Casebook 的管路选型模块直接导入。

三条硬约束:
1. 只转录,不改写:数值照抄原文,不做四舍五入、不做单位换算、不用「典型值」补空。
2. 读不到的格子就留空,并在最后的「缺口清单」里逐条列出,不要猜。
3. 不要输出任何标准原文之外的数据;不确定的行宁可不写。"""

_RULES = """# 格式硬要求
- UTF-8 编码(带 BOM 更好,Excel 双击打开中文表头不乱码);一行表头;逗号分隔。
- 不要合并单元格、不要多层表头、不要小计行、不要在表头前后加说明文字。
- `D_mm` 是外径(mm)、`S_mm` 是壁厚(mm);一个格子只放一个数。
  标准原文里的 `10(10.2)` 这类写法可以原样保留 —— 工具取括号前的名义值。
- 温度列固定命名为 `许用应力_20`、`许用应力_100` 这种形式,温度写摄氏整数,不要写 `20℃`。
- 数值列不带单位、不写「≥」「约」、不用千分位。
- 表 B.1 的「标准」+「牌号」两列必须能对上下列组合(工具只用这 5 个,其余行导进来也不会被用到):
  `GB/T 8163` + `20#`;`GB/T 8163` + `Q345A`;`GB/T 6479` + `Q345B`;
  `GB/T 14976` + `06Cr19Ni10(304)`;`GB/T 14976` + `022Cr17Ni12Mo2(316L)`。"""

_OUTRO = """# 输出方式
按文件逐个输出,每个文件前单独写一行标题(写文件名与来源,不要写进 CSV 本体),然后是代码块里的完整 CSV。
三个文件都要给,不要省略表头。

# 交付前自检
- [ ] 三个文件名分别严格是 t1-17395-table1.csv / t4-17395-table4.csv / t3-20801-tableB1.csv
- [ ] 每张表的行数满足最少行数要求(见上)
- [ ] 表 B.1 至少覆盖上面 5 个「标准 + 牌号」组合中的全部
- [ ] 没有空表头、没有合并单元格、没有单位混进数值列
- [ ] 末尾附「缺口清单」:哪些页/哪些行没读到、为什么"""

_FALLBACK_HEADERS = {
    "t1-17395-table1.csv": ["D_mm", "S_mm"],
    "t4-17395-table4.csv": ["D_mm", "S_mm"],
    "t3-20801-tableB1.csv": ["标准", "牌号", "许用应力_20"],
}


def _example_blocks() -> str:
    """三张表的文件清单 + 表头 + 示例行。示例行直接取模板 CSV,不在这里另抄一份。"""
    from . import data as pipe_data

    preview = pipe_data.template_preview()
    by_name = {f["name"]: f for f in preview.get("files", [])}
    blocks: list[str] = []
    for spec in pipe_data.STD_FILES:
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


def build() -> str:
    """完整的提示词正文。每次调用现拼:模板改了,提示词跟着改。"""
    from . import data as pipe_data

    file_lines = [
        f"- `{spec['name']}` —— {spec['from']},必需列 "
        + "、".join(f"`{c}`" for c in spec["columns"])
        + f",至少 {spec['need']} 行"
        for spec in pipe_data.STD_FILES
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

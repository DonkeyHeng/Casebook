"""app/design/pipe/data.py —— 管路选型用标准数据表(纯数据 + 纯函数)。

数据来源与核对状态(2026-11-07):

| 数据 | 来源 | 状态 |
| --- | --- | --- |
| `WALL_TOLERANCE_T8` / `WALL_TOLERANCE_T9` / `negative_deviation()` | GB/T 17395-2024 表 8 / 表 9(PDF 第 49 页) | ✅ 300 dpi 渲染逐格录入,已一次人工核对 |
| `Y_VALUE` / `Y_MAX_TEMP_FERRITIC_C` / `Y_MAX_TEMP_AUSTENITIC_C` | GB/T 20801.1-2025 表 27 | ✅ 条文已确认(铁素体 ≤482 ℃、奥氏体 ≤566 ℃ 取 0.4) |
| `PN_CODES` / `MATERIAL_CODES` / `INSULATION_CODES` | HG/T 20519.6 | ✅ 见 `docs/pipe-class-codes.md` |
| T1 DN→公称外径、T2 外径×标准壁厚、T3 许用应力 | GB/T 17395 表 1/表 4、GB/T 20801.1 表 B.1 | ✅ 已接入导入流程;原始 CSV 不入库,由用户在「管道」页导入(见 §4) |

页码与分页结构见 `docs/pipe-standards-index.md`。
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app import paths

# --------------------------------------------------------------------------
# 1. 系数与代号表
# --------------------------------------------------------------------------

#: 壁厚公式里的 Y 值(GB/T 20801.1-2025 表 27)。表 27 按温度分档:铁素体钢 ≤482 ℃ 取 0.4,
#: 510 ℃ 档 0.5、538 ℃ 及以上 0.7;奥氏体钢 ≤566 ℃ 取 0.4,593 ℃ 档 0.5、621 ℃ 及以上 0.7。
#: 本工具不按温度切换 Y,一律取 0.4——Y 在分母且为加项,取小值只会算出更厚的壁厚,属保守简化。
#: 超出适用档的工况由 V7(T_d > 450 ℃)提示复核,结果不声称符合表 27 的取值。
Y_VALUE = 0.4
#: 表 27 中 Y = 0.4 的最高温度档,两个钢种的上限不同;仅供文档/校核取用,不参与计算
Y_MAX_TEMP_FERRITIC_C = 482.0
Y_MAX_TEMP_AUSTENITIC_C = 566.0

#: 压力等级代号 → 公称压力 MPa(HG/T 20519.6,国内常用系列)
PN_CODES = {
    "H": 0.25,
    "K": 0.6,
    "L": 1.0,
    "M": 1.6,
    "N": 2.5,
    "P": 4.0,
    "Q": 6.4,
    "R": 10.0,
    "S": 16.0,
    "T": 20.0,
    "U": 22.0,
}

#: 材质类别代号 → 名称(HG/T 20519.6)
MATERIAL_CODES = {
    "A": "铸铁",
    "B": "碳钢",
    "C": "普通低合金钢",
    "D": "合金钢",
    "E": "不锈钢",
    "F": "有色金属",
    "G": "非金属",
    "H": "衬里/内防腐",
}

#: 牌号 → 材质类别代号。表 B.1 的 104 个牌号已逐个确认(2026-11-23,依据用户逐行
#: 判定),不再靠前缀猜;表外牌号仍由 `material_code_for()` 按前缀给建议值。
MATERIAL_CODE_BY_GRADE = {
    # A 铸铁(B 表里这几个的「母材类别」是「—」)
    "HT200": "A",
    "HT250": "A",
    "HT300": "A",
    "HT350": "A",
    "KTH300-06": "A",
    "KTH350-10": "A",
    "QT400-18": "A",
    "QT400-18L": "A",
    # B 碳钢
    "20#": "B",
    "20G": "B",
    "20MnG": "B",
    "25MnG": "B",
    "A105": "B",
    "CC60": "B",
    "CC70": "B",
    "CF370": "B",
    "CF415": "B",
    "CF415K": "B",
    "L210/A (PSL1)": "B",
    "L245/B (PSL1)": "B",
    "L245/B (PSL2)": "B",
    "LCB": "B",
    "LCC": "B",
    "LF415K1": "B",
    "LF415K2": "B",
    "Q215A": "B",
    "Q215B": "B",
    "Q235A": "B",
    "Q235B": "B",
    "Q245R": "B",
    "WCA": "B",
    "WCB": "B",
    "WCC": "B",
    # C 普通低合金钢
    "10MnDG": "C",
    "16MnDG": "C",
    "CF485": "C",
    "CF485K": "C",
    "F290": "C",
    "F360": "C",
    "F415": "C",
    "F450": "C",
    "F485": "C",
    "F555": "C",
    "L290/X42 (PSL2)": "C",
    "L360/X52 (PSL2)": "C",
    "L415/X60 (PSL2)": "C",
    "L450/X65 (PSL2)": "C",
    "L485/X70 (PSL2)": "C",
    "L555/X80 (PSL2)": "C",
    "LF485K2": "C",
    "Q345A": "C",
    "Q345B": "C",
    "Q345D": "C",
    "Q345E": "C",
    "Q345R": "C",
    # D 合金钢(铬钼耐热钢)
    "06Ni3MoDG": "D",
    "06Ni9DG": "D",
    "10Cr9Mo1VNbN": "D",
    "10MoWVNb": "D",
    "12Cr1Mo": "D",
    "12Cr1MoV": "D",
    "12Cr1MoVG": "D",
    "12Cr2Mo": "D",
    "12Cr2Mo1": "D",
    "12Cr2MoG": "D",
    "12Cr5Mo": "D",
    "12Cr5MoI": "D",
    "12Cr5MoNT": "D",
    "12Cr9MoI": "D",
    "12Cr9MoNT": "D",
    "14Cr1Mo": "D",
    "15CrMo": "D",
    "15CrMoG": "D",
    "AF5": "D",
    "AF5G": "D",
    "AF9": "D",
    "AF9G": "D",
    "AF91": "D",
    "AF11": "D",
    "AF11G": "D",
    "AF12": "D",
    "AF12G": "D",
    "AF14": "D",
    "AF22": "D",
    "AF22G": "D",
    "LC3": "D",
    "LC9": "D",
    "LF450K3": "D",
    "LF680K4": "D",
    "ZG10Cr9Mo1- VNbN": "D",
    "ZG12Cr2Mo": "D",
    "ZG14Cr1Mo": "D",
    "ZG16Cr5Mo": "D",
    # E 不锈钢
    "06Cr17Ni12Mo2 (316)": "E",
    "06Cr18Ni11Nb (347)": "E",
    "07Cr17Ni12Mo2 (316H)": "E",
    "07Cr18Ni11Nb (347H)": "E",
    "07Cr19Ni10 (304H)": "E",
    "07Cr19Ni11Ti (321H)": "E",
    "08Cr18Ni11NbFG (347HFG)": "E",
    "S30403": "E",
    "S30408": "E",
    "S31603": "E",
    "S32168": "E",
}

#: 表 B.1 里的牌号写法 → 本项目叫法。同一牌号在标准表里常写成完整牌号
#: (06Cr19Ni10(304)),而模型、导出与等级号都用短代号(S30408);不归一会出现两个名字,
#: 下拉里看着像两种材料、等级号却对不上。
GRADE_ALIASES = {
    "06Cr19Ni10(304)": "S30408",
    "022Cr17Ni12Mo2(316L)": "S31603",
    "022Cr19Ni10(304L)": "S30403",
    "06Cr18Ni11Ti(321)": "S32168",
}

#: 表 B.1 之外的牌号才走这里(前缀建议)。表内 104 个牌号已在
#: `MATERIAL_CODE_BY_GRADE` 里逐个确认,不会再到这一步。
#: 铬钼耐热钢(1Cr5Mo、9Cr1Mo、12Cr1MoV、5CrMo…)先于不锈钢规则识别:这些牌号
#: 数字个数与 0?[0-9]Cr\d 短式重合,若不前置会被静默判成 E(不锈钢)。表内铬钼钢
#: 都已确认成 D;表外的落"待人工确认",不装作判出来了。
HEAT_RESISTANT_GRADE_RULES: tuple[re.Pattern[str], ...] = (re.compile(r"^\d+Cr(\d+)?Mo", re.I),)

MATERIAL_CODE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # 新国标超低碳不锈钢(如 022Cr19Ni10、022Cr17Ni12Mo2):三位数字前导 0,
    # 单独一支覆盖;不能把 0?[0-9]Cr 扩成 0?[0-9]{1,2}Cr,否则会误命中 12Cr1MoV。
    (
        re.compile(
            r"^(S\d{5}|0[0-9]{2}Cr\d|0?[0-9]Cr\d|1Cr\d|2Cr\d|3Cr\d)",
            re.I,
        ),
        "E",
    ),  # 不锈钢
    (re.compile(r"^(Q235|10#|20#|20G|25#)", re.I), "B"),  # 碳钢
    (re.compile(r"^(Q\d{3}|16Mn|15Mn)", re.I), "C"),  # 低合金钢
    (re.compile(r"^(TA\d|Ti)", re.I), "F"),  # 钛材
    (re.compile(r"^(PVC|PP|PE|PTFE|HDPE|FRP)", re.I), "G"),  # 非金属
)

#: 绝热代号(HG/T 20519.6;C/P/T 需人工勾选)
INSULATION_CODES = {
    "H": "保温",
    "C": "保冷",
    "P": "人身防护防烫",
    "T": "伴热",
}

#: 本项目口径:保温代号 -H 按**设计温度**判定。设计温度 = 操作 + 20 ℃(§17.4/§17.8),
#: 故设计 ≥ 65 ℃ ⟺ 操作 ≥ 45 ℃ —— 25~45 ℃ 常温管线不再误判包保温。
INSULATION_ON_TEMP_C = 65.0

# --------------------------------------------------------------------------
# 1.1 工程校核补充规则(2026-11-18 口径修订)
# --------------------------------------------------------------------------
# 这些值都是**可配置的工程判据**,不是从标准表抄来的数字;改口径只改这里,
# 通过 options 传入可逐工况覆盖。依据与边界写在 docs/pipe-selection-plan.md §17。

#: 真空管路的**默认**等级代号。HG/T 20519-2009 里 V=25.0 MPa(高压),不能再把 V
#: 借给真空用;工程上真空管法兰/阀门按最低压力等级 PN10 配级,故默认取 L(PN1.0)。
#: 真空判定本身只看布尔 `is_vacuum`(绝压 < 95 kPa),与这个字母无关;
#: 特殊项目可用 options["vacuum_pn_code"] 覆写代号。
VACUUM_PN_CODE = "L"

# --------------------------------------------------------------------------
# 1.2 第三轮工程口径(2026-11-18,§19)
# --------------------------------------------------------------------------
#: 流股类型:Aspen 的 `\Data\Streams\<id>` 节点属性 6 实测为 MATERIAL / HEAT / WORK。
#: 只有 MATERIAL 进选型;热量流(Q1–Q4)与功流不进《未选型流股》清单。
MATERIAL_STREAM_TYPES = ("MATERIAL",)
#: 老库没有 stream_type 列:只有当名字形如 Q1 / W2 且六项物料结果全空时,
#: 才按遗留热量流 / 功流剔除,避免把没 Run 的物料流股误删(§19.1)。
LEGACY_NONMATERIAL_NAME_PATTERN = r"^(?:Q|W)\d+$"

#: 真空判定阈值(绝压 kPa)。101.3 → 95.0:工程建模常把常压流股设成 1 bar = 100 kPa,
#: 老阈值会把这种带微压降的常压线误判成真空;95.0 kPa 对应表压 −6.3 kPa(§19.1)。
VACUUM_ABS_KPA = 95.0
#: 深真空(绝压 ≤ 10 kPa)专项:严控吸入压降防闪蒸 + 高温弹性模量折减(§19.2)。
DEEP_VACUUM_ABS_KPA = 10.0
DEEP_VACUUM_HOT_TEMP_C = 250.0

#: DN1000 焊接管 5% 向上容差:Φ1016 × 10 mm → 标准内径 996 mm,
#: +5% = 1045 mm。计算内径落 [996, 1045] 的流股选 DN1000,不踢入未选型(§19.3)。
OVERSIZE_TOLERANCE = 0.05
DN1000_STD_INNER_MM = 996.0
DN1000_TOL_MAX_MM = 1045.0

#: 温度专项备注用的操作温度门槛(℃,§19.4)。
LOW_TEMP_NOTE_C = -20.0
HIGH_TEMP_NOTE_C = 300.0

#: 非优先公称直径 → 自动上圆整到的优先档。DN65/DN125 不在优先系列里,
#: 计算内径落进这两档时直接取 DN80/DN150,不再出现在选型结果中(§17.2)。
DISALLOWED_DN = {65: 80, 125: 150}

#: 设计温度裕量(℃)。规范给 15–30 ℃,默认取中值 20,且**永远往危险侧取**:
#: 操作温度 ≥0 ℃ 时 T_d = t + 裕量,<0 ℃ 时 T_d = t − 裕量(偏冷侧)。
#: 绝热代号按**设计温度**判:≥65 ℃ 追加 -H、≤0 ℃ 追加 -C(§17.4)。
#: 低温段若照旧 +20 ℃,整条低温管会被抬高 20 K,保冷 -C 与 V7 一起漏判。
DESIGN_TEMP_MARGIN_C = 20.0
DESIGN_TEMP_MARGIN_RANGE_C = (15.0, 30.0)

#: 高压不锈钢升档:操作绝压落在 [4.7, 6.4] MPa、材质 S30408/S31603 的流股,
#: 等级压力代号强制升到 R(10.0 MPa)并按 §17.8 公式重算壁厚 —— 防止高温段因
#: 许用应力下降反而比低温段更薄(§17.3)。温度仅作佐证,不是硬门槛。
#: 下限 4.7(原 4.8):把 4700 kPa 的 PG-0116/PL-0125 一并纳入,避免与 4800 kPa
#: 在 Q1E/R1E 间跳档(2026-11-18 第二轮修订)。
# 上限 5.6 → 6.4:5.6–6.4 的设计压力原落 PN Q 档,56.1 bar 的壁厚会从 7.99 掉到 5.44
# (压力越高壁越薄的倒挂);6.4 以上 P_d > 6.4 已由 pn_code_for()=R 走 R1E,不必放到 10
# (那会削薄 9.5–10 MPa、本应吃 PN16 等级保护的壁厚)。
HP_SS_PRESSURE_BAND_MPA = (4.7, 6.4)
HP_SS_GRADES = ("S30408", "S31603")
HP_SS_FORCE_CODE = "R"

#: R1E 不锈钢壁厚公式参数(§17.8 第二轮口径,用户指定):
#: **项目专用、非标准公式,不声称对 GB/T 20801.1 或其他标准的符合性。**
#: t = P·D_o/(2[σ]^t·E_j + P) + C,再除以负偏差系数 0.875,向上圆整到标准壁厚序列。
#: 分母压力项固定为 P(等效 Y=0.5),与标准通式按 Y 查表的写法不同,不能反向当作标准实现。
#: E_j 焊缝系数(无缝管 1.0)、C 腐蚀裕量(1.0 mm)、负偏差系数(12.5% → 0.875)。
#: C 只是**回退值**:用户在管道等级表里给过腐蚀裕量时按用户值算(见 selection.py)。
HP_SS_WELD_FACTOR = 1.0
HP_SS_CORROSION_MM = 1.0
HP_SS_NEG_DEV_FACTOR = 0.875
#: 小口径机械强度保护:R1E 小管(如 DN15)最小壁厚 3 mm —— 2 mm 薄壁在高压下
#: 接口螺纹/支承削弱风险大(§17.8)。公式算出的更厚时仍按公式。
HP_SS_MIN_WALL_MM = 3.0

#: S30408 高压公式用许用应力 [σ]^t(MPa,按设计温度插值)。**用户 2026-11-18 第二轮
#: 指定口径**,比录回的 GB/T 20801.1 表 B.1 略保守(如 150 ℃:123 对 129),专用于
#: R1E 升档族的壁厚计算;**越界不外推**:≤100 ℃ 钳位取 137 MPa,>350 ℃ 返回 None
#: (蠕变段必须人工给 [σ]^t,与通用表 B.1「不录不猜」一致)。
HP_SS_STRESS_S30408 = {
    100.0: 137.0,
    150.0: 123.0,
    200.0: 115.0,
    250.0: 109.0,
    300.0: 104.0,
    350.0: 100.0,
}

#: 近常压沸点液体的汽蚀限速:液相 + 80–105 ℃ + 绝压 95–115 kPa(微正压/常压吸入侧)
#: 视为泵吸入易汽蚀工况,流速上限压到 0.8 m/s(§17.2/§17.8)。120 kPa 以上的微正压
#: 排出侧不再误触发;泵排出口/明显升压管线由拓扑另行排除。
CAVITATION_TEMP_RANGE_C = (80.0, 105.0)
CAVITATION_ABS_KPA_RANGE = (95.0, 115.0)
CAVITATION_VELOCITY_MS = 0.8
#: 「明显升压」判定阈值(MPa):流股压力比上游同流量同温度流股高出它,视为泵排出口
#: 加压管线,不触发汽蚀限速(§17.8)。
CAVITATION_PRESSURE_RISE_MPA = 0.05

#: 大口径气液两相流:DN ≥ 800 时均相 ρv² 判据不足以描述分层流/波状流,
#: 打风险标记;DN1000 焊接管最小壁厚提到 10 mm 保刚度(§17.2)。
STRATIFIED_FLOW_MIN_DN = 800
WELDED_DN1000_MIN_WALL_MM = 10.0

#: 高温大口径真空管(工况52 PG-0105 340℃/DN600 一类):高温下弹性模量显著下降,
#: 外压筛查的常温 E 已不足,壁厚下限 10 mm(规范给 8–10,取保守端),备注提示
#: 「高温外压失稳复核」(§17.8)。
VACUUM_HOT_TEMP_C = 250.0
VACUUM_HOT_MIN_DN = 400
VACUUM_HOT_MIN_WALL_MM = 10.0
#: DN ≥ 700 真空焊接管(如 PGL-0519 DN900):大径薄壁真空管光靠壁厚不经济,
#: 备注标注「需设外压加强圈,严禁全真空抽吸」(§17.8)。
VACUUM_STIFFENER_MIN_DN = 700

#: 真空外压(失稳)校核:长圆筒弹性屈曲 P_cr = 2E/(1-ν²)·(t/Do)³,
#: 取外压 0.1 MPa、安全系数 3、E=180 GPa(不锈钢全温区保守值)、ν=0.3。
#: 算出 t_min 后与内压壁厚取大;结果只作筛查,仍需按实际支撑跨距复核(§17.1)。
VACUUM_EXTERNAL_P_MPA = 0.1
VACUUM_BUCKLING_SF = 3.0
VACUUM_BUCKLING_E_MPA = 180000.0
VACUUM_BUCKLING_NU = 0.3
VACUUM_EXTERNAL_MIN_DN = 150

#: 零流量阈值(m³/h):低于它视作备用/放空管线,不按常规物流定死管径(§17.5)。
ZERO_FLOW_M3H = 0.01

#: 标准流股号 = 四位流水号;AB/BC 这类非标准编号只高亮、不强行重映射(§17.5)。
STREAM_NO_PATTERN = r"^\d{4}$"


# --------------------------------------------------------------------------
# 2. 壁厚允许偏差(GB/T 17395-2024 表 8 / 表 9,C1 的来源)
# --------------------------------------------------------------------------
#
# 规则表示法:
#   ("sym", pct, abs_mm)  →  ±pct·S 或 ±abs_mm,取其中的较大值
#   ("lower", pct)        →  上偏差不做要求,下偏差 −pct·S
#
# 表 8 按 S/D 分档:((S/D 上限, 规则), ...);上限 None 表示"全区间适用"。
# 注意:表 8 存在**跨列合并格**——S3-B 行的第 1 格横跨 S/D≤0.025 与 0.025~0.05 两列,
#      所以它的第一条是 (0.05, ...) 而不是 (0.025, ...)。核对方法见
#      docs/pipe-standards-index.md §4.1.1(按单元格竖线的墨迹占比数格子)。

_INF = float("inf")

WALL_TOLERANCE_T8 = {
    "S1": ((None, ("sym", 0.15, 0.60)),),
    "S2-A": ((None, ("sym", 0.125, 0.40)),),
    "S2-B": ((None, ("lower", 0.125)),),
    "S3-A": ((None, ("sym", 0.10, 0.20)),),
    "S3-B": (
        (0.05, ("sym", 0.15, 0.40)),  # 该格在原表横跨 S/D≤0.025 与 0.025~0.05 两列
        (0.1, ("sym", 0.125, 0.40)),
        (_INF, ("sym", 0.10, 0.40)),
    ),
    "S3-C": ((None, ("lower", 0.10)),),
    "S4-A": ((None, ("sym", 0.075, 0.15)),),
    "S4-B": (
        (0.025, ("sym", 0.15, 0.20)),
        (0.05, ("sym", 0.125, 0.20)),
        (0.1, ("sym", 0.10, 0.20)),
        (_INF, ("sym", 0.075, 0.20)),
    ),
    "S5": ((None, ("sym", 0.05, 0.10)),),
}

#: 表 9 非标准化壁厚允许偏差:(上偏差百分比, 下偏差百分比),都是相对 S
WALL_TOLERANCE_T9 = {
    "NS1": (0.15, -0.125),
    "NS2": (0.15, -0.10),
    "NS3": (0.125, -0.10),
    "NS4": (0.125, -0.075),
}

#: 无缝钢管的常用负偏差等级。GB/T 8163 一类输送流体用无缝钢管按 −12.5% 计,
#: 对应表 8 的 S2-B / 表 9 的 NS1;要改口径只改这一处。
DEFAULT_WALL_TOL_GRADE = "S2-B"


def _ratio(d_mm: float, s_mm: float) -> float:
    """S/D。d_mm 为公称外径,不允许为 0。"""
    if d_mm <= 0:
        raise ValueError(f"公称外径必须为正数: {d_mm!r}")
    return s_mm / d_mm


def _pick_rule(bands, ratio: float):
    """从 ((上限, 规则), ...) 里挑出命中的规则。"""
    for upper, rule in bands:
        if upper is None or ratio <= upper:
            return rule
    return None


def negative_deviation(
    d_mm: float,
    s_mm: float,
    grade: str = DEFAULT_WALL_TOL_GRADE,
    table: str = "T8",
) -> float:
    """返回**壁厚负偏差的绝对值**(mm),也就是公式里的 C1。

    参数
    ----
    d_mm : 公称外径,mm
    s_mm : 公称壁厚,mm(圆整**之后**的标准壁厚)
    grade : 表 8 的偏差等级(`S2-B` 等)或表 9 的(`NS1` 等)
    table : `"T8"` 标准化壁厚偏差、`"T9"` 非标准化壁厚偏差

    说明
    ----
    * 表 8 的 ±X 是"±pct·S 或 ±abs_mm 取较大值",所以负偏差 = max(pct·S, abs);
    * 表 8 的 S2-B / S3-C 只规定下偏差,负偏差 = pct·S;
    * 表 8 没有空白格;某档为 None 只可能是抄漏,函数会返回 0.0 并暴露出来,不要当成"无要求";
    * 非标准化表(表 9)直接给上下偏差百分比,负偏差 = |下偏差| · S。
    """
    if table.upper() == "T9":
        if grade not in WALL_TOLERANCE_T9:
            raise KeyError(f"表 9 没有偏差等级 {grade!r};可选:{sorted(WALL_TOLERANCE_T9)}")
        _up, down = WALL_TOLERANCE_T9[grade]
        return abs(down) * s_mm

    if grade not in WALL_TOLERANCE_T8:
        raise KeyError(f"表 8 没有偏差等级 {grade!r};可选:{sorted(WALL_TOLERANCE_T8)}")
    rule = _pick_rule(WALL_TOLERANCE_T8[grade], _ratio(d_mm, s_mm))
    if rule is None:
        return 0.0
    if rule[0] == "sym":
        _kind, pct, abs_mm = rule
        return max(pct * s_mm, abs_mm)
    if rule[0] == "lower":
        return rule[1] * s_mm
    raise ValueError(f"未知的偏差规则:{rule!r}")


def min_wall_for_negative_deviation(
    t_required_mm: float, grade: str = DEFAULT_WALL_TOL_GRADE
) -> float:
    """负偏差只往下做时,反解"至少要选多厚",使 t_required ≤ S·(1 − pct)。

    只处理表 9 / 表 8 里"百分比负偏差"的情形(表 8 的 S2-B、S3-C、表 9 全部);
    表 8 里带 `或 ±abs` 的等级不能这样反解(要按 S 迭代),那种情况直接调
    `negative_deviation()` 逐档试算。
    """
    if grade in WALL_TOLERANCE_T9:
        pct = abs(WALL_TOLERANCE_T9[grade][1])
    else:
        rules = WALL_TOLERANCE_T8.get(grade)
        if not rules or len(rules) != 1 or rules[0][1] is None or rules[0][1][0] != "lower":
            raise ValueError(
                f"{grade!r} 不是纯百分比负偏差等级(带 或 ±abs 的要按 S 逐档试算,不能反解)"
            )
        pct = rules[0][1][1]
    return t_required_mm / (1.0 - pct)


# --------------------------------------------------------------------------
# 3. DN → 公称外径(化工 Φ / B 系列)
# --------------------------------------------------------------------------
#
# 来源与核对状态(2026-11-22):
#   * DN10–DN40 的 Φ 值是 B 系列惯例,标 unverified;
#   * DN50–DN600 的 Φ 值已逐个在 GB/T 17395-2024 表 1 的 D 列确认存在;
#   * DN700–DN1000 的 DN→Φ 按 HG/T 20553-2011 表 2.0.2-2 的 Ia 系列确认:
#     711 / 813 / 914 / 1016,四个外径同时属于 GB/T 17395-2024 表 1 系列 1;
#   * DN 与 Φ 的对应关系本身是**工程惯例**(不是 GB/T 17395 定义的),
#     不能写成"GB/T 17395 系列 I 换算"。
DN_TO_OD: dict[int, float] = {
    10: 14.0,
    15: 18.0,
    20: 25.0,
    25: 32.0,
    32: 38.0,
    40: 45.0,
    50: 57.0,
    65: 76.0,
    80: 89.0,
    100: 108.0,
    125: 133.0,
    150: 159.0,
    200: 219.0,
    250: 273.0,
    300: 325.0,
    350: 377.0,
    400: 426.0,
    450: 480.0,
    500: 530.0,
    600: 630.0,
    # DN>600 走卷制/焊接管:无缝管到 Φ630 为止。**口径分两段,不要混**:
    #   * DN10–600  = GB/T 17395 **系列 II**(化工 Φ 系列:108/159/…/630);
    #   * DN700–1000 = HG/T 20553-2011 表 2.0.2-2 **Ia 系列**
    #     (711/813/914/1016,同时属 GB/T 17395-2024 表 1 系列 1)。
    # 保留 Ia、不切换到 II 系列 720/820/920/1020:换系列会改内径 → 壁厚 / 流速 / 等级号
    # 全变,历史工况不可比。壁厚按 WELDED_WALL_SERIES_BY_OD 的实际采购档取值。
    700: 711.0,
    800: 813.0,
    900: 914.0,
    1000: 1016.0,
}

#: 外径已在标准表里核对过的 DN。
#: 2026-11-22:DN700–1000 的 711/813/914/1016 已按 HG/T 20553-2011 表 2.0.2-2
#: 的 Ia 系列逐档核对,并确认同时属于 GB/T 17395-2024 表 1 系列 1。
DN_OD_VERIFIED = {dn for dn in DN_TO_OD if dn <= 600} | {700, 800, 900, 1000}


# --------------------------------------------------------------------------
# 3.1 工艺管道最小壁厚下限(工程惯例,不是尺寸表)
# --------------------------------------------------------------------------
#
# 为什么需要这一层:GB/T 20801.1 的壁厚公式只管**承压**。1.0 MPa、DN15 的
# 不锈钢管按公式只要 0.07 mm,圆整到标准壁厚是 0.5 mm —— 那是换热管/仪表管的
# 规格,工艺管道焊不出来,也扛不住支承、运输与装卸(标准 4.1.6.2 明确要求把
# 腐蚀/冲蚀/螺纹深度/支承等裕量算进"最小厚度")。所以小口径工艺管道的壁厚
# 实际由**最小壁厚**控制,不是由承压控制。
#
# 取值(2026-11-07 用户拍板):DN10–25 → 2.0;DN32–65 → 3.0;DN80–100 → 3.0;
# DN125 起不设限。只按"能施工"定:小口径工艺管道的最小壁厚由焊接、刚度与支承
# 要求控制,与承压无关;故取偏下限,不追求贴近任何外部表格。
# 改之前先看 docs/pipe-selection-plan.md §15.2。
#
# 键是 **DN 上限**:`{25: 2.0}` 表示 DN ≤ 25 管子壁厚不小于 2.0 mm。
MIN_WALL_BY_DN: dict[int, float] = {
    25: 2.0,
    65: 3.0,
    100: 3.0,
    # 2026-11 扩到大口径:原来 DN125 起不设限,结果两相流放大到 DN125 后
    # 落到了 1.6 mm(那是换热管/薄壁管的规格,工艺管焊不出来)。这几档仍按
    # "施工刚度"分档,不是标准值,可按项目口径整体调整。
    150: 3.5,
    300: 4.5,
    600: 5.5,
    # 2026-11-18:DN1000 焊接管保刚度下限提到 10 mm(§17.2),覆盖原 6.5。
    1000: WELDED_DN1000_MIN_WALL_MM,
}

#: 工艺管道最小公称直径:DN10 只作仪表/采样预留,不参与常规物流自动选型。
#: 主工艺线跑到 DN10 会被流速/压降/机械强度同时否掉,现场也不会这么配。
MIN_DN_PROCESS = 15

#: 大口径焊接管(>DN600)的 Ia 系列采购壁厚档(HG/T 20553-2011 表 2.0.2-2)。
#: 表内无该外径时仍按本序列兜底并标复核,但不再用 6/8/10/12/14/16/18/20 的整数近似:
#: DN700/800/900 的 711/813/914 从 6.3 起;DN1000 的 1016 没有 6.3/7.1,从 8.0 起。
_IA_WALLS = (6.3, 7.1, 8.0, 8.8, 10.0, 11.0, 12.5, 14.2, 16.0, 17.5, 20.0)
_IA_WALLS_DN1000 = (8.0, 8.8, 10.0, 11.0, 12.5, 14.2, 16.0, 17.5, 20.0)
WELDED_WALL_SERIES: tuple[float, ...] = _IA_WALLS
WELDED_WALL_SERIES_BY_OD: dict[float, tuple[float, ...]] = {
    711.0: _IA_WALLS,
    813.0: _IA_WALLS,
    914.0: _IA_WALLS,
    1016.0: _IA_WALLS_DN1000,
}


def welded_wall_series(od_mm: float | None) -> tuple[float, ...]:
    """大口径焊接管按外径取 Ia 系列采购壁厚档;未知外径回退通用 Ia 序列。"""
    if od_mm is None:
        return WELDED_WALL_SERIES
    return WELDED_WALL_SERIES_BY_OD.get(round(float(od_mm), 1), WELDED_WALL_SERIES)


#: 保冷后缀 -C 的温度阈值(℃):低于它就认为需要保冷。
COLD_INSULATION_BELOW_C = 0.0


def minimum_wall(dn: int | None) -> float | None:
    """DN → 最小壁厚下限 mm;`None` 表示不设限。"""
    if dn is None:
        return None
    for upper in sorted(MIN_WALL_BY_DN):
        if dn <= upper:
            return float(MIN_WALL_BY_DN[upper])
    return None


# --------------------------------------------------------------------------
# 4. 从 data/ 加载标准表(不放进公开仓库)
# --------------------------------------------------------------------------
#
# 为什么不在代码里写死:
#   * 这些数值来自国标表格,按项目约定"标准原文不入库,只把规则摘要写进 docs";
#   * 录入是外部完成的,数据在 data/preview/std/docx/*.csv(已被 .gitignore 覆盖),
#     这里只负责读进来,读不到就保持为空 —— 页面会显示"未录入",绝不拿 0 去算。
#
# 数据来源与核对(2026-11-07):
#   * 表 1 / 表 4:GB/T 17395-2024,由外部视觉录入(D≤630、S≤25);
#   * 表 B.1:GB/T 20801.1-2025,同上;原件温度首列是 40 ℃(没有 20 ℃ 列),
#     所以低于 40 ℃ 的取值按 allowable_stress() 的"取最低温度列"处理;
#   * 表 1 的 kg/m 已用 m = 0.02466·S·(D−S) 反算校验:2795 格里 2784 格误差 <0.5%,
#     11 格可疑且集中在 D≤18 与 D406 的 S24/25(本项目选型范围之外),不影响使用。

_CURATED_DIR = paths.data_dir("preview", "std", "docx")

#: 只含表头与 2 行虚构数值的格式模板(随仓库分发、不含任何真实标准数据)。
#: 页面「下载 CSV 模板」与填写提示词都读这里;exe 打包时
#: `tools/build_exe.py` 会把这个目录一起塞进只读资源根。
TEMPLATE_DIR = paths.resource_root() / "samples" / "pipe-standards-template"

# 标准表改成"版本化目录 + 原子切换":
#   data/preview/std/versions/<时间戳>/   每个版本一整套 CSV
#   data/preview/std/current.txt          当前生效的是哪个版本(一行)
# 导入时先在 versions/ 里拼一份完整目录、试加载通过,再改指针 —— 不再逐个
# CSV 覆盖到老位置,不会出现"新的壁厚表配旧的许用应力表"。
# 没有指针时退回老位置 docx/,已有安装不用迁移。
_STD_ROOT = paths.data_dir("preview", "std")
_LEGACY_DIR = _CURATED_DIR
_VERSIONS_DIR = _STD_ROOT / "versions"
_POINTER_PATH = _STD_ROOT / "current.txt"

#: 导入与计算共用一把锁:导入过程中不许有另一次导入,也不许读到半套表
_STANDARDS_LOCK = threading.RLock()


@dataclass(frozen=True)
class StandardsSnapshot:
    """三张表的**一次性**快照 —— 计算侧只认这个,不零散地读模块变量。"""

    od_to_walls: dict[float, tuple[float, ...]]
    od_to_walls_sst: dict[float, tuple[float, ...]]
    stress: dict[str, dict[float, float]]
    source: str = ""


def _read_pointer() -> str:
    try:
        return _POINTER_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_pointer(name: str) -> None:
    """原子切换版本:写临时文件再 os.replace(同目录、同盘)。"""
    _STD_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = _POINTER_PATH.with_name(_POINTER_PATH.name + ".tmp")
    tmp.write_text(name + "\n", encoding="utf-8")
    os.replace(tmp, _POINTER_PATH)


def _resolve_active_dir() -> Path:
    """当前生效的标准表目录:有版本指针用版本目录,否则退回 docx/。"""
    name = _read_pointer()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return _LEGACY_DIR
    candidate = _VERSIONS_DIR / name
    return candidate if candidate.is_dir() else _LEGACY_DIR


def _refresh_active_dir() -> None:
    global _CURATED_DIR
    _CURATED_DIR = _resolve_active_dir()


def _fill_staging(staging: Path, base: Path, accepted: dict[str, str]) -> None:
    """拼出"导入后长什么样":给了新内容的用新的,没给的从当前版本复制。"""
    for spec in STD_FILES:
        name = spec["name"]
        if name in accepted:
            (staging / name).write_text(accepted[name].lstrip("\ufeff"), encoding="utf-8-sig")
        elif (base / name).is_file():
            shutil.copy2(base / name, staging / name)


def _check_staged(staging: Path, accepted: dict[str, str]) -> list[str]:
    """照真实逻辑把 staging 试加载一遍;返回错误列表(空 = 通过)。"""
    errors: list[str] = []
    od, sst = _load_od_to_walls(staging)
    stress = _stress_from_rows(_read_csv(staging / "t3-20801-tableB1.csv"))
    if "t1-17395-table1.csv" in accepted and not od:
        errors.append("表 1 一个外径都没读出来:检查 D_mm / S_mm 两列的值")
    if "t4-17395-table4.csv" in accepted and not sst:
        errors.append("表 4 一个外径都没读出来:检查 D_mm / S_mm 两列的值")
    if "t3-20801-tableB1.csv" in accepted and not stress:
        errors.append(
            "表 B.1 一行许用应力都没读出来:检查「许用应力_<温度>」列是不是数值,"
            "以及「牌号」列抄的是不是材料牌号(不是尺寸或标准号)"
        )
    return errors


#: 表 B.1 里每个本项目牌号**取哪一行**(同一牌号有多个标准/尺寸档时)
_STRESS_PICK = {
    "20#": ("GB/T 8163", "20"),  # 流体输送用无缝钢管
    "Q345A": ("GB/T 8163", "Q345A"),
    "Q345B": ("GB/T 6479", "Q345B"),  # 三个尺寸档取各温度下的最小值
    "S30408": ("GB/T 14976", "06Cr19Ni10(304)"),
    "S31603": ("GB/T 14976", "022Cr17Ni12Mo2(316L)"),
}

#: 录进来的表 B.1 每行原始数据(留着给"计算书"标注来源)
STRESS_ROWS: list[dict] = []


# --------------------------------------------------------------------------
# 4.1 表没录时,告诉使用者「要做什么」(页面与导出都直接显示这段)
# --------------------------------------------------------------------------
#
# 为什么仓库里不带这些数据:数值来自 GB/T 国家标准原文,受著作权保护,
# 不能随开源项目分发(Release 里也不放),所以仓库里是空目录 + 这份说明。
# 使用者需要自己从合法渠道取得标准,再整理成 CSV 放进 data/preview/std/docx/。

SETUP_GUIDE: dict = {
    "why": "这些数值来自 GB/T 国家标准原文,受著作权保护,不能随本项目分发,"
    "所以仓库和安装包里都不带。请从合法渠道取得标准后自行整理一份。",
    "dir": "data/preview/std/docx",
    "doc": "docs/pipe-standards-index.md",
    "prompt": "docs/pipe-standards-extract-prompt.md",
    "standards": [
        {
            "code": "GB/T 17395-2024",
            "title": "无缝钢管尺寸、外形、重量及允许偏差",
            "tables": "表 1(普通钢管)、表 4(不锈钢管);本工具用到 D ≤ 630、S ≤ 25 的部分",
        },
        {
            "code": "GB/T 20801.1-2025",
            "title": "压力管道规范 工业管道 第 1 部分:总则",
            "tables": "表 B.1(材料许用应力)",
        },
    ],
    "steps": [
        "取得上面两份标准(正版纸质/电子,或单位订阅的标准库),找到对应表。",
        "按下面的文件清单整理成 3 个 CSV(UTF-8、第一行表头);每张表在哪一页、"
        "分页怎么拼,见 docs/pipe-standards-index.md。",
        "整理时可以整段复制 docs/pipe-standards-extract-prompt.md 里的提示词给一个能看图的 AI,"
        "连同标准页面一起交给它,让它照格式抄成 CSV。",
        "把 3 个 CSV 导进来:回首页点「标准表 › 导入 / 更新」,选管路那组的文件 → 导入,"
        "马上就能用;也可以手工放进 data/preview/std/docx/ 后重启服务。",
        "回到「管道」页,上面的状态会变成「已录」,管道表自动算出来。",
    ],
    "files": [
        {
            "name": "t1-17395-table1.csv",
            "from": "GB/T 17395-2024 表 1",
            "columns": "D_mm, S_mm(其余列可空)",
        },
        {
            "name": "t4-17395-table4.csv",
            "from": "GB/T 17395-2024 表 4",
            "columns": "D_mm, S_mm(其余列可空)",
        },
        {
            "name": "t3-20801-tableB1.csv",
            "from": "GB/T 20801.1-2025 表 B.1",
            "columns": "标准, 牌号, 许用应力_20 … 400(每个温度一列)",
        },
    ],
}


def setup_guide() -> dict:
    """标准表没录时给使用者的操作说明(深拷贝,避免调用方改到常量)。"""
    return json.loads(json.dumps(SETUP_GUIDE, ensure_ascii=False))


#: 标准表里的“没有值”写法:空、半角/全角横杠、短破折号都按缺值放行。
_MISSING_CELL_TOKENS = {"", "-", "—", "–", "--", "－"}
_OD_RANGE_MM = (1.0, 3000.0)
_WALL_RANGE_MM = (0.1, 100.0)
_STRESS_RANGE_MPA = (1.0, 1000.0)
_STD_DELIMITERS = (",", ";", "\t")


def _std_csv_rows(text) -> list[dict]:
    """读标准表 CSV:兼容 Excel 的 , ; Tab、「sep=;」首行与表头前后空格/BOM。"""
    raw = str(text or "").lstrip("\ufeff")
    lines = raw.splitlines()
    delimiter = ","
    if lines:
        head = lines[0].strip().lower()
        if head.startswith("sep="):
            candidate = lines[0].split("=", 1)[1].strip()[:1]
            if candidate in _STD_DELIMITERS:
                delimiter = candidate
            raw = "\n".join(lines[1:])
        else:
            counts = {d: lines[0].count(d) for d in _STD_DELIMITERS}
            best = max(counts, key=lambda d: counts[d])
            if counts[best] > 0:
                delimiter = best
    reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)
    rows = []
    for row in reader:
        rows.append(
            {str(k or "").strip().lstrip("\ufeff"): v for k, v in row.items() if k is not None}
        )
    return rows


def _read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open("rb") as f:
        text = f.read().decode("utf-8-sig")
    return _std_csv_rows(text)


def _parse_size(raw: str | None) -> float | None:
    """`10(10.2)` / `2.2(2.3)` → 名义值 10 / 2.2;解析不了返回 None。"""
    if raw is None or not str(raw).strip():
        return None
    m = re.match(r"^\s*(?:\([^)]*\)\s*)?([0-9]+(?:\.[0-9]+)?)", str(raw))
    return float(m.group(1)) if m else None


def _load_od_to_walls(
    dir_: Path | None = None,
) -> tuple[dict[float, tuple[float, ...]], dict[float, tuple[float, ...]]]:
    """表 1(碳钢/普通钢管)与表 4(不锈钢管)→ {外径: 标准壁厚序列}。

    `dir_` 只在导入时用到(先往临时目录里试加载);平时读 `data/preview/std/docx`。
    """
    base = Path(dir_) if dir_ else _CURATED_DIR
    carbon: dict[float, list[float]] = {}
    for row in _read_csv(base / "t1-17395-table1.csv"):
        d = _parse_size(row.get("D_mm"))
        s = _parse_size(row.get("S_mm"))
        if d and s:
            carbon.setdefault(d, []).append(s)
    sst: dict[float, list[float]] = {}
    for row in _read_csv(base / "t4-17395-table4.csv"):
        d = _parse_size(row.get("D_mm"))
        s = _parse_size(row.get("S_mm"))
        if d and s:
            sst.setdefault(d, []).append(s)
    return (
        {d: tuple(sorted(set(v))) for d, v in carbon.items()},
        {d: tuple(sorted(set(v))) for d, v in sst.items()},
    )


def project_grade(raw: str) -> str:
    """表 B.1 里的牌号写法 → 本项目的牌号叫法(查 `GRADE_ALIASES`,不认识就原样)。

    比对时忽略空格:全表里写的是「06Cr19Ni10 (304)」,别名表写的是
    「06Cr19Ni10(304)」,只差括号前一个空格,不能因此漏掉归一。
    """
    text = str(raw or "").strip()
    squeezed = text.replace(" ", "")
    for alias, name in GRADE_ALIASES.items():
        target = alias.replace(" ", "")
        if squeezed == target or squeezed.startswith(target):
            return name
    return text


def material_code_for(grade: str) -> tuple[str | None, str]:
    """牌号 → (HG/T 20519.6 材质类别代号, 来源)。

    来源四种:`explicit`(已确认映射,表 B.1 的 104 个牌号都在这里)、
    `prefix`(表外牌号按前缀推断)、`heat_resistant`(表外铬钼耐热钢,类别未确认)、
    `unknown`(判不出来)。判不出来时调用方按 E 暂记并告警,不在这里替用户拍板。
    """
    text = str(grade or "").strip()
    if not text:
        return None, "unknown"
    code = MATERIAL_CODE_BY_GRADE.get(text)
    if code:
        return code, "explicit"
    for pattern in HEAT_RESISTANT_GRADE_RULES:
        if pattern.match(text):
            return None, "heat_resistant"
    for pattern, code in MATERIAL_CODE_RULES:
        if pattern.match(text):
            return code, "prefix"
    return None, "unknown"


#: 牌号至少要有这些字符之一(字母或 #),且不能太长。用来把"牌号列其实抄的是尺寸"
#: 这类错位挡在外面 —— 例如 Φ57×3.5、112.5 都不像材料牌号。
_GRADE_SHAPE = re.compile(r"[A-Za-z#]")


def looks_like_grade(text: str) -> bool:
    """牌号写法是否像个牌号(形状判断,不判断牌号真假)。"""
    value = str(text or "").strip()
    return bool(value) and len(value) <= 24 and bool(_GRADE_SHAPE.search(value))


def _stress_from_rows(rows: list[dict]) -> dict[str, dict[float, float]]:
    """表 B.1 的行 → {本项目牌号: {温度: 许用应力 MPa}}(不碰模块状态)。

    两组来源:
    * `_STRESS_PICK` 里的预置牌号:按固定「标准 + 牌号」取行(Q345B 三个尺寸档取
      各温度最小值),保证放开全表后历史工况的结论不变;
    * 其余牌号:表里录了什么就收什么,同一牌号多行(多尺寸档 / 多标准)同样取最小值。
      牌号先经 `project_grade()` 归一到项目叫法,避免 06Cr19Ni10(304) 与 S30408
      变成两种材料。
    """
    temps = {}
    for key in rows[0].keys() if rows else []:
        m = re.fullmatch(r"许用应力_(\d+)", key or "")
        if m:
            temps[int(m.group(1))] = key
    temps = dict(sorted(temps.items()))
    if not temps:
        return {}

    def table_of(matched: list[dict]) -> dict[float, float]:
        table: dict[float, float] = {}
        for t, col in temps.items():
            vals = []
            for r in matched:
                raw = (r.get(col) or "").strip()
                if raw:
                    try:
                        vals.append(float(raw))
                    except ValueError:
                        pass
            if vals:
                # 多行并存时取保守值(最小),与 Q345B 原来的口径一致
                table[float(t)] = min(vals)
        return table

    out: dict[str, dict[float, float]] = {}

    # ① 预置牌号:固定取行规则优先(牌号比对忽略空格,全表写法带「(304)」前的空格)
    for grade, (std, mark) in _STRESS_PICK.items():
        target = mark.replace(" ", "")
        matched = [
            r
            for r in rows
            if r.get("标准") == std
            and (mark in ("", "*") or str(r.get("牌号", "")).replace(" ", "").startswith(target))
        ]
        if not matched:
            continue
        table = table_of(matched)
        if table:
            out[grade] = table

    # ② 其余牌号:表里有什么收什么,已被 ① 收过的跳过
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        raw_grade = str(r.get("牌号") or "").strip()
        if not raw_grade:
            continue
        if not looks_like_grade(raw_grade):
            continue
        name = project_grade(raw_grade)
        if name in out:
            continue
        grouped.setdefault(name, []).append(r)
    for name, matched in grouped.items():
        table = table_of(matched)
        if table:
            out[name] = table
    return out


#: 牌号 → 表 B.1 的「母材类别」(Fe-1 / Fe-4 / Fe-5A …)。只用于下拉分组,
#: 不参与壁厚计算;铸铁行的「母材类别」是「—」。
GRADE_CATEGORY: dict[str, str] = {}
CAST_IRON_CATEGORY = "—"


def _load_stress(dir_: Path | None = None) -> dict[str, dict[float, float]]:
    """表 B.1 → {本项目牌号: {温度: 许用应力 MPa}}。"""
    global STRESS_ROWS
    base = Path(dir_) if dir_ else _CURATED_DIR
    rows = _read_csv(base / "t3-20801-tableB1.csv")
    STRESS_ROWS = rows
    _refresh_grade_category(rows)
    return _stress_from_rows(rows)


def _refresh_grade_category(rows: list[dict]) -> None:
    """记录「牌号 → 母材类别」。同键多行时优先取有类别的(铸铁行是「—」)。"""
    GRADE_CATEGORY.clear()
    for row in rows:
        grade = project_grade(row.get("牌号"))
        if not grade:
            continue
        cat = (row.get("母材类别") or "").strip() or CAST_IRON_CATEGORY
        if GRADE_CATEGORY.get(grade) in (None, CAST_IRON_CATEGORY):
            GRADE_CATEGORY[grade] = cat
    # 预置牌号里有的不是表里的写法(20# 取的是「GB/T 8163 + 20」那一行),
    # 按 _STRESS_PICK 回查来源行补类别,否则下拉里会漏掉这些默认材质
    for preset, (std, mark) in _STRESS_PICK.items():
        if GRADE_CATEGORY.get(preset) not in (None, CAST_IRON_CATEGORY):
            continue
        for row in rows:
            if (row.get("标准") or "").strip() != std:
                continue
            if (row.get("牌号") or "").strip() != mark:
                continue
            GRADE_CATEGORY[preset] = (row.get("母材类别") or "").strip() or CAST_IRON_CATEGORY
            break


def _category_sort_key(cat: str) -> tuple[int, int, str]:
    """Fe-2 排在 Fe-11A 前面;铸铁/非钢管永远垫底。"""
    if cat == CAST_IRON_CATEGORY:
        return (1, 0, "")
    m = re.match(r"Fe-(\d+)(.*)", cat)
    if m:
        return (0, int(m.group(1)), m.group(2))
    return (0, 999, cat)


def grade_groups() -> list[dict]:
    """表 B.1 的牌号按母材类别分组,给前端下拉做 optgroup。

    `steel=False` 的是铸铁/非钢管(表 B.1 里「母材类别」为空或「—」的那几行),
    前端默认折叠,勾「显示全部牌号」才展开。
    """
    order: list[str] = []
    for cat in GRADE_CATEGORY.values():
        if cat not in order:
            order.append(cat)
    order.sort(key=_category_sort_key)
    groups = []
    for cat in order:
        grades = sorted(g for g, c in GRADE_CATEGORY.items() if c == cat and g in ALLOWABLE_STRESS)
        if not grades:
            continue
        groups.append(
            {
                "label": "铸铁/非钢管" if cat == CAST_IRON_CATEGORY else cat,
                "category": cat,
                "steel": cat != CAST_IRON_CATEGORY,
                "grades": grades,
            }
        )
    return groups


OD_TO_WALLS: dict[float, tuple[float, ...]]
OD_TO_WALLS_SST: dict[float, tuple[float, ...]]
_refresh_active_dir()
OD_TO_WALLS, OD_TO_WALLS_SST = _load_od_to_walls()
ALLOWABLE_STRESS: dict[str, dict[float, float]] = _load_stress()

_CURRENT_SNAPSHOT = StandardsSnapshot(
    OD_TO_WALLS, OD_TO_WALLS_SST, ALLOWABLE_STRESS, str(_CURATED_DIR)
)


def tables_snapshot() -> StandardsSnapshot:
    """取当前这一套三张表(同一次加载的结果)。"""
    with _STANDARDS_LOCK:
        return _CURRENT_SNAPSHOT


def _apply_snapshot(snap: StandardsSnapshot) -> None:
    """一次性替换三张表与快照。调用方必须持 _STANDARDS_LOCK。"""
    global OD_TO_WALLS, OD_TO_WALLS_SST, ALLOWABLE_STRESS, _CURRENT_SNAPSHOT
    OD_TO_WALLS = snap.od_to_walls
    OD_TO_WALLS_SST = snap.od_to_walls_sst
    ALLOWABLE_STRESS = snap.stress
    _CURRENT_SNAPSHOT = snap


# --------------------------------------------------------------------------
# 4.2 从页面导入标准表(免得手工放 CSV 再重启服务)
# --------------------------------------------------------------------------
#
# 三张表的数据同样受著作权保护,只能由使用者自己整理;这里只做四件事:
# 收文件 → 校验 → 写进 data/preview/std/docx/ → 立即重新加载。
#
# 校验分两层,目的是"宁可拒绝,不留半份坏数据":
#   ① 单个文件:文件名在白名单里、UTF-8 能读、表头有必需列、行数够;
#   ② 合起来能不能用:把新文件与已有的拼进一个临时目录,先照真实逻辑加载一遍,
#      外径 / 壁厚 / 许用应力一个都读不出来就整单拒绝,一个字节都不写进去。
#
# 导入后不重启服务 —— 直接替换本模块的三份表,下一次算管道就用新数据。

_ROOT = paths.app_root()

#: 三个 CSV 的规格。页面按它渲染上传位,后端按它校验;与 `SETUP_GUIDE["files"]` 对应。
STD_FILES: tuple[dict, ...] = (
    {
        "name": "t1-17395-table1.csv",
        "label": "表 1 · 普通钢管",
        "from": "GB/T 17395-2024 表 1",
        "columns": ["D_mm", "S_mm"],
        "need": 10,  # 最少数据行;"够不够用"另有临时目录试加载兜着
    },
    {
        "name": "t4-17395-table4.csv",
        "label": "表 4 · 不锈钢管",
        "from": "GB/T 17395-2024 表 4",
        "columns": ["D_mm", "S_mm"],
        "need": 10,
    },
    {
        "name": "t3-20801-tableB1.csv",
        "label": "表 B.1 · 材料许用应力",
        "from": "GB/T 20801.1-2025 表 B.1",
        "columns": ["标准", "牌号"],
        "need": 1,
    },
)

#: 单个文件上限。表 1 那张三万多行也就 100 KB 上下,8 MB 足够。
MAX_STD_FILE_BYTES = 8 * 1024 * 1024


def std_file_spec(name: str) -> dict | None:
    """文件名 → 规格;不在白名单里返回 None。"""
    for spec in STD_FILES:
        if spec["name"] == name:
            return spec
    return None


def _dir_label(base: Path) -> str:
    try:
        return base.relative_to(_ROOT).as_posix()
    except ValueError:
        return str(base)


def standards_status(dir_: Path | None = None) -> dict:
    """三个 CSV 现在有没有、多少行、什么时候放进来的(只看文件,不看加载结果)。"""
    base = Path(dir_) if dir_ else _CURATED_DIR
    files = []
    for spec in STD_FILES:
        path = base / spec["name"]
        item = {
            "name": spec["name"],
            "label": spec["label"],
            "from": spec["from"],
            "columns": list(spec["columns"]),
            "exists": False,
            "rows": 0,
            "bytes": 0,
            "mtime": None,
        }
        if path.is_file():
            st = path.stat()
            item.update(
                {
                    "exists": True,
                    "rows": len(_read_csv(path)),
                    "bytes": st.st_size,
                    "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                }
            )
        files.append(item)
    return {"dir": _dir_label(base), "files": files}


# ---------------------------------------------------------------- 格式模板
#: 模板只有表头 + 2 行虚构数值:用来对列名,不能当数据。导入时按文本比对挡掉,
#: 免得有人把示例里的 111.1 / 11.1 当真实规格导进去算出一张看着像真的表。


def _norm_csv_text(text) -> str:
    """比较模板用的归一化:去 BOM、统一换行、去行尾空白。"""
    raw = str(text or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in raw.split("\n")).strip()


def template_texts() -> dict[str, str]:
    """三份模板的归一化文本;读不到的那份不出现在结果里(不回退、不造)。"""
    out: dict[str, str] = {}
    for spec in STD_FILES:
        path = TEMPLATE_DIR / spec["name"]
        try:
            out[spec["name"]] = _norm_csv_text(path.read_text(encoding="utf-8-sig"))
        except OSError:
            continue
    return out


def is_template_text(text) -> bool:
    """是不是原封不动的那份示例模板。"""
    norm = _norm_csv_text(text)
    if not norm:
        return False
    return any(norm == t for t in template_texts().values())


def template_preview() -> dict:
    """填写提示词要用的示例:表头 + 示例行 + 导入门槛。"""
    files = []
    for spec in STD_FILES:
        path = TEMPLATE_DIR / spec["name"]
        rows = _read_csv(path)
        headers = list(rows[0].keys()) if rows else list(spec["columns"])
        files.append(
            {
                "name": spec["name"],
                "label": spec["label"],
                "from": spec["from"],
                "columns": list(spec["columns"]),
                "need": spec["need"],
                "headers": headers,
                "rows": [[str(r.get(h) or "") for h in headers] for r in rows],
            }
        )
    return {
        "dir": "samples/pipe-standards-template",
        "exists": all((TEMPLATE_DIR / s["name"]).is_file() for s in STD_FILES),
        "files": files,
        "note": (
            "示例数值为虚构,仅示列名与写法。表 1 / 表 4 至少 10 行、表 B.1 至少 1 行才能导入;"
            "模板原样导入会被拒绝。"
        ),
    }


def _canonical_std_text(text) -> str:
    """把已校验通过的文本统一成逗号分隔、规范表头,再写盘(运行时只认这一种)。"""
    rows = _std_csv_rows(text)
    if not rows:
        return str(text or "")
    headers = list(rows[0].keys())
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def _is_missing_cell(raw) -> bool:
    return raw is None or str(raw).strip() in _MISSING_CELL_TOKENS


def _parse_std_number(raw) -> float | None:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _check_std_sizes(rows: list[dict], need: int) -> tuple[list[str], dict]:
    """表 1/表 4:逐格查外径、壁厚的数值与物理区间,不再只看列名。"""
    errors: list[str] = []
    pairs = 0
    ods: set[float] = set()
    for i, row in enumerate(rows, start=2):
        d_raw = row.get("D_mm")
        s_raw = row.get("S_mm")
        if _is_missing_cell(d_raw) and _is_missing_cell(s_raw):
            continue
        d = _parse_size(d_raw)
        s = _parse_size(s_raw)
        if d is None or s is None:
            errors.append(
                f"第 {i} 行:外径/壁厚读不出({str(d_raw).strip()!r} / {str(s_raw).strip()!r})"
            )
            continue
        if not (_OD_RANGE_MM[0] <= d <= _OD_RANGE_MM[1]):
            errors.append(f"第 {i} 行:外径 {d:g} mm 超出 1–3000 mm,疑似串列")
        if not (_WALL_RANGE_MM[0] <= s <= _WALL_RANGE_MM[1]):
            errors.append(f"第 {i} 行:壁厚 {s:g} mm 超出 0.1–100 mm,疑似串列")
        if d <= 2 * s:
            errors.append(f"第 {i} 行:外径 {d:g} mm 不大于 2×壁厚 {s:g} mm,数据不合理")
        pairs += 1
        ods.add(d)
    if pairs < need:
        errors.append(
            f"能读出「外径 + 壁厚」的只有 {pairs} 行(至少 {need} 行),检查 D_mm / S_mm 是不是数值"
        )
    return errors[:12], {"rows": len(rows), "sizes": pairs, "od_count": len(ods)}


def _check_std_stress(rows: list[dict]) -> tuple[list[str], dict]:
    """表 B.1:空/横杠按缺值放行;真填了内容却读不出或超出 1–1000 MPa 才报错。"""
    stress_cols = [c for c in rows[0] if c.startswith("许用应力_")]
    if not stress_cols:
        return ["没有许用应力列(许用应力_<温度>),表 B.1 至少要一档"], {}
    errors: list[str] = []
    warnings: list[str] = []
    grades: set[str] = set()
    for i, row in enumerate(rows, start=2):
        grade = str(row.get("牌号") or "").strip()
        if grade:
            grades.add(grade)
        else:
            errors.append(f"第 {i} 行:牌号为空")
        seq: list[tuple[int, float]] = []
        for col in stress_cols:
            raw = row.get(col)
            if _is_missing_cell(raw):
                continue
            value = _parse_std_number(raw)
            if value is None:
                errors.append(f"第 {i} 行 {col}:{str(raw).strip()!r} 不是有效数字")
                continue
            if not (_STRESS_RANGE_MPA[0] <= value <= _STRESS_RANGE_MPA[1]):
                errors.append(
                    f"第 {i} 行 {col}:{value:g} MPa 超出 1–1000 MPa,疑似把温度/压力抄进应力列"
                )
                continue
            try:
                seq.append((int(col.rsplit("_", 1)[1]), value))
            except ValueError:
                pass
        # 软告警:许用应力随温度一般只会降不会升;缺值点已跳过,不参与比较
        for (t0, v0), (t1, v1) in zip(seq, seq[1:], strict=False):
            if v1 > v0 + max(1.0, 0.02 * abs(v0)):
                warnings.append(
                    f"第 {i} 行 {grade or '?'}:{t0} ℃ {v0:g} → {t1} ℃ {v1:g} MPa 上升,"
                    "疑似串列/串行,建议复核"
                )
                break
    return errors[:12], {
        "rows": len(rows),
        "grades": sorted(grades)[:12],
        "warnings": warnings[:8],
    }


def _validate_std_text(spec: dict, text) -> tuple[list[str], dict]:
    """校验单个 CSV 的文本,返回 (错误列表, 统计)。"""
    if not isinstance(text, str) or not text.strip():
        return ["文件是空的"], {}
    if "\ufffd" in text:
        return ["里面有乱码:多半不是 UTF-8 编码。用 Excel「另存为 → CSV UTF-8」再试"], {}
    if len(text.encode("utf-8")) > MAX_STD_FILE_BYTES:
        return [f"文件超过 {MAX_STD_FILE_BYTES // (1024 * 1024)} MB,不像标准表"], {}
    try:
        rows = _std_csv_rows(text)
    except csv.Error as exc:
        return [f"CSV 解析失败:{exc}"], {}
    if not rows:
        return ["只有表头、没有数据行"], {}
    names = list(rows[0].keys())
    missing = [c for c in spec["columns"] if c not in names]
    if missing:
        return [f"缺列 {', '.join(missing)};现在的表头是:{', '.join(names[:8])}"], {}
    if len(rows) < spec["need"]:
        return [f"只有 {len(rows)} 行数据(至少 {spec['need']} 行),像是没抄全"], {}
    if spec["name"].startswith("t3-"):
        return _check_std_stress(rows)
    return _check_std_sizes(rows, spec["need"])


def reload_standards(dir_: Path | None = None) -> dict:
    """重新读三个 CSV,并**一次性**替换模块里的三张表 —— 导入后立即生效,不用重启。"""
    base = Path(dir_) if dir_ else _CURATED_DIR
    with _STANDARDS_LOCK:
        old = _CURRENT_SNAPSHOT
    try:
        od, sst = _load_od_to_walls(base)
        stress = _load_stress(base)
    except Exception:
        with _STANDARDS_LOCK:  # 读坏了保留旧的,别把页面打空
            _apply_snapshot(old)
        raise
    snap = StandardsSnapshot(od, sst, stress, str(base))
    with _STANDARDS_LOCK:
        _apply_snapshot(snap)
    return {
        "od_to_walls": len(od),
        "od_to_walls_sst": len(sst),
        "stress_grades": sorted(stress),
        "source": str(base),
    }


def import_standards(
    payload, *, target_dir: Path | str | None = None, reload: bool = True
) -> tuple[bool, list[str], dict]:
    """把页面选中的 CSV 写进标准表目录,并(默认)立即重新加载。

    `payload` 形如 `{"files": {"t1-17395-table1.csv": "<CSV 文本>", ...}}`,
    返回 `(是否写入, 错误列表, 当前文件状态)`。只要有一个文件不合格就整单不写。
    `target_dir` / `reload` 是给自测用的:导入到临时目录、不碰运行中的表。
    """
    base = Path(target_dir) if target_dir else _CURATED_DIR
    data = payload if isinstance(payload, dict) else {}
    files = data.get("files")
    if not isinstance(files, dict) or not files:
        return False, ["没有收到文件:先选要导入的 CSV 再点导入"], standards_status(base)

    errors: list[str] = []
    warnings: list[str] = []
    accepted: dict[str, str] = {}
    for name, text in files.items():
        spec = std_file_spec(str(name))
        if spec is None:
            errors.append(
                f"不认识的文件名 {name!r};只收 " + "、".join(s["name"] for s in STD_FILES)
            )
            continue
        if is_template_text(text):
            # 模板是给人对列名用的,数值是编的;真导进去会算出一张看着像真的假表
            errors.append(
                f"{name}:这是随项目分发的格式模板(示例数值),不是标准表原文 —— "
                "按模板的列名填入真实数据后再导入"
            )
            continue
        errs, info = _validate_std_text(spec, text)
        if errs:
            errors.extend(f"{name}:{e}" for e in errs)
            continue
        warnings.extend(f"{name}:{w}" for w in info.get("warnings", []))
        accepted[spec["name"]] = _canonical_std_text(text)
    if errors:
        return False, errors, standards_status(base)

    # 自测走 target_dir:直接写那个临时目录,不建版本、不动指针
    if target_dir is not None:
        ok, errs, status = _import_into_dir(base, accepted, reload=reload)
        status["warnings"] = warnings
        return ok, errs, status

    # 正式路径:先在 versions/ 下组装**完整**目录,试加载通过后原子切换指针。
    # 此前逐文件覆盖生效目录:后续文件失败时前者已生效,页面会得到新壁厚表
    # 配旧许用应力表。
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = _VERSIONS_DIR / stamp
    seq = 1
    while staging.exists():
        seq += 1
        staging = _VERSIONS_DIR / f"{stamp}-{seq}"
    try:
        staging.mkdir(parents=True)
        _fill_staging(staging, base, accepted)
        errors = _check_staged(staging, accepted)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return False, [f"写入失败:{exc}"], standards_status(base)
    if errors:
        shutil.rmtree(staging, ignore_errors=True)
        return False, errors, standards_status(base)

    previous = _read_pointer()
    try:
        _write_pointer(staging.name)
        _refresh_active_dir()
        if reload:
            reload_standards()
    except Exception as exc:
        # 指针切过去之后读不起来 → 退回上一个版本,别让页面拿到空表
        try:
            if previous:
                _write_pointer(previous)
            else:
                _POINTER_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        _refresh_active_dir()
        shutil.rmtree(staging, ignore_errors=True)
        return False, [f"导入后加载失败,已回退:{exc}"], standards_status()
    status = standards_status()
    status["warnings"] = warnings
    return True, [], status


def _import_into_dir(
    base: Path, accepted: dict[str, str], *, reload: bool
) -> tuple[bool, list[str], dict]:
    """自测路径:直接写进指定目录(不建版本、不动指针)。"""
    staging = base.parent / f".std-import-{os.getpid()}-{time.time_ns()}"
    try:
        base.mkdir(parents=True, exist_ok=True)
        staging.mkdir()
        try:
            _fill_staging(staging, base, accepted)
            errors = _check_staged(staging, accepted)
            if errors:
                return False, errors, standards_status(base)
            for name in accepted:
                (staging / name).replace(base / name)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    except OSError as exc:
        return False, [f"写入失败:{exc}"], standards_status(base)
    if reload:
        reload_standards(base)
    return True, [], standards_status(base)


#: 本期 DN 上限(超过就标黄提示"超出 DN600,需专项确认")
MAX_DN = 1000  # 无缝到 DN600,再往上按焊接管(见 DN_TO_OD 末尾四档)


# --------------------------------------------------------------------------
# 5. 介质 → 管道等级(材质 / 腐蚀裕量):用户定义,工具不猜
# --------------------------------------------------------------------------
#
# 工程上"这个介质该用什么材质"由设计人员按**管道等级表**定,不是拿组分算出来的:
# 腐蚀取决于浓度、温度、含水量、氯离子 ppm、流速……同一物质在不同工况下结论可以相反
# (干氯气对碳钢几乎不腐蚀,痕量水就变成剧腐蚀)。所以工具只做两件事:
#   ① 按表**一致地套用**材质与腐蚀裕量;② 把**表里没定义过**的流股明确暴露出来。
# 它不判断某种介质是否腐蚀、也不给你推荐牌号 —— 那属于材料/腐蚀专业的活儿。
#
# 配置文件:`data/config/piping-classes.json`(`data/` 已 gitignore)
#   {
#     "rules": [
#       {"stream_prefix": ["CW", "CWR"], "grade": "20#", "corrosion_mm": 1.0, "note": "循环冷却水"},
#       {"medium": ["PL", "PGL"], "grade": "S30408", "corrosion_mm": 0.0, "note": "工艺液体"}
#     ],
#     "default": {"grade": "S30408", "corrosion_mm": 0.0}
#   }
# 没配这个文件也能跑:退回全局默认材质,不提示"未定义"。

_CONFIG_DIR = paths.data_dir("config")

#: 规则列表(按顺序匹配,先命中先用)
PIPING_CLASS_RULES: list[dict] = []
#: 兜底等级(可为 None,表示"没匹配就退回全局默认材质")
PIPING_CLASS_DEFAULT: dict | None = None


def _load_piping_classes() -> tuple[list[dict], dict | None]:
    path = _CONFIG_DIR / "piping-classes.json"
    if not path.is_file():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], None  # 配置写坏了当没配,不要因此让整个选型挂掉
    rules = [r for r in (data.get("rules") or []) if isinstance(r, dict)]
    default = data.get("default") if isinstance(data.get("default"), dict) else None
    return rules, default


PIPING_CLASS_RULES, PIPING_CLASS_DEFAULT = _load_piping_classes()


def resolve_piping_class(
    stream: str, medium: str, rules: list[dict] | None = None, default: dict | None = None
) -> dict:
    """按「流股名前缀 + 介质代号」查管道等级。

    返回 `{grade, corrosion_mm, note, source}`:
    `source="rule"` 命中某条规则、`"default"` 落兜底、`"none"` 都没命中
    (由调用方决定是标"待人工指定"还是退回全局默认材质)。
    """
    use_rules = PIPING_CLASS_RULES if rules is None else rules
    use_default = PIPING_CLASS_DEFAULT if default is None else default
    name = str(stream or "").upper()
    for rule in use_rules:
        prefixes = rule.get("stream_prefix") or []
        media = rule.get("medium") or []
        if prefixes and not any(name.startswith(str(p).upper()) for p in prefixes):
            continue
        if media and medium not in media:
            continue
        return {
            "grade": rule.get("grade"),
            "corrosion_mm": rule.get("corrosion_mm"),
            "note": rule.get("note", ""),
            "source": "rule",
        }
    if use_default:
        return {
            "grade": use_default.get("grade"),
            "corrosion_mm": use_default.get("corrosion_mm"),
            "note": use_default.get("note", ""),
            "source": "default",
        }
    return {"grade": None, "corrosion_mm": None, "note": "", "source": "none"}


def piping_class_status() -> dict:
    """管道等级表的配置状态 —— 给页面/接口显示"现在是配了表还是全按默认"。

    默认口径:**不填表就全部按页面上的默认材质**(默认 S30408),不会因为"没配"而少算或报错。
    """
    return {
        "configured": bool(PIPING_CLASS_RULES),
        "rule_count": len(PIPING_CLASS_RULES),
        "has_default": PIPING_CLASS_DEFAULT is not None,
        "default_grade": (PIPING_CLASS_DEFAULT or {}).get("grade"),
        "note": "" if PIPING_CLASS_RULES else "未配置管道等级表:全部流股按页面上的默认材质计算",
    }


PIPING_CLASS_PATH = _CONFIG_DIR / "piping-classes.json"

#: 介质代号可以填的取值(空列表 = 不限制介质)
PIPING_CLASS_MEDIA = ("PL", "PG", "PGL")


def piping_class_grades() -> tuple[str, ...]:
    """等级表与管材下拉可选的牌号。

    以**已录入的表 B.1** 为准:录了什么牌号就能选什么牌号(含 06Cr19Ni10(304)
    这类完整写法,已归一成 S30408)。表没录时退回 5 个预置牌号,保证空环境下页面
    仍可用;此时页面的标准表状态会明确显示"未录入",不会让人以为表已经生效。
    """
    grades = tuple(sorted(ALLOWABLE_STRESS))
    return grades or tuple(sorted(MATERIAL_CODE_BY_GRADE))


def reload_piping_classes() -> None:
    """重新读配置文件 —— 页面保存后调用,生效不用重启服务。"""
    global PIPING_CLASS_RULES, PIPING_CLASS_DEFAULT
    PIPING_CLASS_RULES, PIPING_CLASS_DEFAULT = _load_piping_classes()


def validate_piping_classes(payload) -> tuple[dict, list[str]]:
    """清洗用户提交的等级表,返回 (清洗后, 错误列表)。

    只做**结构与取值**校验:牌号必须在许用应力表里、腐蚀裕量是非负数、
    前缀/介质至少给一个 —— 至于"这个介质该不该用这个材质"不在这里判断。
    """
    errors: list[str] = []
    grades_ok = piping_class_grades()
    data = payload if isinstance(payload, dict) else {}
    raw_rules = data.get("rules") or []
    if not isinstance(raw_rules, list):
        return {"rules": [], "default": None}, ["rules 必须是数组"]

    def _list(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [str(v).strip().upper() for v in value if str(v).strip()]

    rules: list[dict] = []
    for i, raw in enumerate(raw_rules, 1):
        if not isinstance(raw, dict):
            errors.append(f"第 {i} 条不是对象")
            continue
        prefixes = _list(raw.get("stream_prefix"))
        media = _list(raw.get("medium"))
        grade = str(raw.get("grade") or "").strip()
        if not prefixes and not media:
            errors.append(f"第 {i} 条:前缀和介质至少填一个")
            continue
        if grade not in grades_ok:
            errors.append(
                f"第 {i} 条:牌号 {grade!r} 不在已录入的表 B.1 里(可用 {'/'.join(grades_ok)})"
            )
            continue
        bad_media = [m for m in media if m not in PIPING_CLASS_MEDIA]
        if bad_media:
            errors.append(
                f"第 {i} 条:介质代号 {bad_media} 不认识(可用 {'/'.join(PIPING_CLASS_MEDIA)})"
            )
            continue
        c2 = raw.get("corrosion_mm")
        try:
            c2 = 0.0 if c2 in (None, "") else float(c2)
        except (TypeError, ValueError):
            errors.append(f"第 {i} 条:腐蚀裕量必须是数字")
            continue
        # NaN 能同时骗过 c2 < 0 和 c2 > 20 两个比较,得单独挡
        if not math.isfinite(c2) or c2 < 0 or c2 > 20:
            errors.append(f"第 {i} 条:腐蚀裕量要在 0–20 mm 之间")
            continue
        rule = {"grade": grade, "corrosion_mm": c2}
        if prefixes:
            rule["stream_prefix"] = prefixes
        if media:
            rule["medium"] = media
        note = str(raw.get("note") or "").strip()
        if note:
            rule["note"] = note[:80]
        rules.append(rule)

    default = None
    raw_default = data.get("default")
    if isinstance(raw_default, dict) and str(raw_default.get("grade") or "").strip():
        grade = str(raw_default.get("grade")).strip()
        if grade not in grades_ok:
            errors.append(f"兜底牌号 {grade!r} 不在已录入的表 B.1 里(可用 {'/'.join(grades_ok)})")
        else:
            try:
                c2 = float(raw_default.get("corrosion_mm") or 0.0)
            except (TypeError, ValueError):
                c2 = 0.0
            if not math.isfinite(c2):
                # min/max 对 NaN 不起作用,NaN 会被原样带进配置
                c2 = 0.0
            default = {"grade": grade, "corrosion_mm": max(0.0, min(c2, 20.0))}
    return {"rules": rules, "default": default}, errors


def save_piping_classes(payload) -> tuple[bool, list[str]]:
    """校验 → 写文件 → 重新加载。返回 (是否写入, 错误列表)。"""
    if not isinstance(payload, dict):
        return False, ["请求体必须是 JSON 对象"]
    if "rules" not in payload and "default" not in payload:
        # "没收到内容"不等于"你要清空":删配置文件必须显式提交空表,
        # 否则一个缺 body 或字段拼错的请求就能把等级表清掉。
        return False, [
            '请求体里没有 rules / default;清空等级表请显式提交 {"rules": [], "default": null}'
        ]
    cleaned, errors = validate_piping_classes(payload)
    if errors:
        return False, errors
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not cleaned["rules"] and not cleaned["default"]:
        # 空表 = 清除配置,回到"全部按默认材质"(已由上面的显式字段校验把关)
        try:
            PIPING_CLASS_PATH.unlink(missing_ok=True)
        except OSError as exc:
            return False, [f"删除配置失败:{exc}"]
        reload_piping_classes()
        return True, []
    payload_out = {"_说明": "由管道页的「等级表」按钮生成;删掉本文件等于回到默认材质", **cleaned}
    try:
        PIPING_CLASS_PATH.write_text(
            json.dumps(payload_out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        return False, [f"写入配置失败:{exc}"]
    reload_piping_classes()
    return True, []

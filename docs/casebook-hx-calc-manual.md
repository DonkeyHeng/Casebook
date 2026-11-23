# 换热器选型计算说明书

**Casebook · 计算说明书**

- 专业：换热专业 · 计算册
- 版本 v1.8.0 · 2026-11-23 · Casebook
- 说明：本说明书解释 L1 与 L2 几何初选口径，不替代 EDR rating、GB/T 151 强度校核与工程设计审核

> 面向需要复算与核对的使用者 · 口径以本工具当前版本的实际计算为准。

## 目录

- 1 适用范围与计算链
- 2 输入与单位
- 3 L1 计算公式
  - 3.1 对数平均温差
  - 3.2 热负荷与反算 U
  - 3.3 需求面积与裕量
  - 3.4 设计压力
  - 3.5 设计温度
  - 3.6 允许压降
  - 3.7 污垢热阻
- 4 五态判定与拦截规则
- 5 溯源标签
  - 5.1 四项来源
  - 5.2 页面与导出的显示口径
- 6 型号编码
- 7 输出
  - 7.1 换热器选型一览表 15 列
  - 7.2 整本 Excel「换热器」页
  - 7.3 AI 包 `heat_exchangers.csv`
  - 7.4 EDR 输入清单（三页签）
- 8 人工决定与默认参数库
- 9 边界与未实现
- 10 复算算例（V15 实测 EX0101）
- 11 常数与默认表
- 12 L2 几何初选
  - 12.1 物性读取与缓存
  - 12.2 cp 由热负荷反推
  - 12.3 标准系列表与版本
  - 12.4 缺表降级
  - 12.5 几何枚举与管程水力
  - 12.6 壳程 Kern 估算
  - 12.7 接管圆整
  - 12.8 总传热系数与五态判定
  - 12.9 三页签 EDR 与人工覆盖
- 附录 A　核心代码函数映射表
- 附录 B　版本记录

## 1 适用范围与计算链

本说明书描述 Casebook「换热」页对一台 Aspen 换热类设备（HeatX / MHeatX / Heater / HeaterX）做 L1 选型整理的完整算法：读哪些字段、按什么顺序算、哪些情形不计算、每个结果标什么来源。

L1 只做四件事：

1. **整理工况**：从连接流股与公用工程节点取冷热两侧进出口温度、热负荷、汽化率；
2. **自算 LMTD**：逆流对数平均温差，允许 F 修正但 F 默认 1；
3. **反算 U 与需求面积**：用 Aspen 简捷面积反算 U，再按经验 U 与裕量算需求面积；
4. **给设计条件与编码**：设计压力、设计温度、允许压降、污垢热阻、GB/T 151 型号编码。

L1 **不做**：EDR rating、h/Δp 详细计算、振动分析、SW6 强度校核、两相分段、空冷器、板式换热器。因此 L1 自身的判定上限是「参考」，不输出「合格 / 不合格」。

**L2（Phase 2）**在 L1 之上做几何初选：按用户导入的 GB/T 28712.2 标准系列表枚举管数 / 管长 / 管径 / 管程组合，用管程 Dittus-Boelter 与壳程 Kern 估算两侧膜系数，算出总传热系数与两侧压降，再对每个候选给五态判定（见第 12 章）。L2 仍是简捷校核：不含 EDR 详细分区、折流板漏流修正、振动与强度校核，`pass` 只表示「几何量级自洽」，不是可施工结论。

**表 1-1 计算链总览**

| 步骤 | 做什么 | 落到输出 |
| --- | --- | --- |
| S0 | 识别换热类型；MHeatX / 多端口直接拦截 | kind / blockers |
| S1 | 从连接关系取冷热两侧流股，取进出口温度、热负荷、汽化率 | sides / duty_kw |
| S2 | 工艺侧相变、近等温、温度交叉检查 | blockers |
| S3 | 公用工程：Aspen 节点温度 > 手填 > 预设 > 缺失 | utility / utility_source |
| S4 | 逆流 LMTD；F 缺省为 1 并标未修正 | lmtd.self_c / f |
| S5 | 反算 U = Q/(A·ΔTm·F)；经验 U 与裕量算需求面积 | u.back_calc / area.required_m2 |
| S6 | 设计压力、设计温度、允许压降 | design.tube / design.shell |
| S7 | 查污垢档；管/壳程分配规则 | fouling / tube_fluid |
| S8 | GB/T 151 型号编码，缺项用 ? 占位 | model_code |
| S9 | 五态判定 + 溯源标签 | verdict / provenance |

> **注：** 顺序不可交换：先判定相变与温度交叉，再算 LMTD；否则会拿一个没有定义的温差去反算 U。

## 2 输入与单位

| 输入 | 来源 | 单位 | 缺省行为 |
| --- | --- | --- | --- |
| 热负荷 `duty_kw` | Aspen `HX_DUTY` / 设备热负荷 | kW | 缺 → `MISSING_DUTY` 拦截 |
| 热侧 / 冷侧温度 | 连接流股 `temperature_c` | ℃ | 工艺侧缺 → `MISSING_PROCESS_TEMPS` |
| 汽化率 | 流股 `vap_frac` | 0–1 | 缺按 0 处理，不拦截 |
| 面积 `area_m2` | Aspen `HX_AREAC/AREAC2/AREAP` | m² | 缺 → `area.source = none` |
| `UA` | Aspen `HX_UA` | W/K（读入时由 cal/sec-K 换算） | 缺不拦截，仅不显示 |
| `HX_DTLM` | Aspen 简捷 | ℃ | 仅作对照，不替代自算 |
| 操作压力 | 流股 `pressure_bar` | bar（绝） | 缺 → 设计压力空 |
| 公用工程温度 | 公用工程节点 `TEMP` | ℃ | 缺 → 预设 / 手填 / 拦截 |

单位约定：温差 ℃ 与 K 数值相同，不做 +273.15；热负荷取绝对值算 U，符号单独保留；压力统一 bar（绝），设计压力输出 bar。

## 3 L1 计算公式

### 3.1 对数平均温差

逆流端差：

```text
ΔT1 = T_h,in − T_c,out
ΔT2 = T_h,out − T_c,in
ΔTm = (ΔT1 − ΔT2) / ln(ΔT1/ΔT2)
```

判据：

- `ΔT1 ≤ 0` 或 `ΔT2 ≤ 0` → 温度交叉，`lmtd = None`，拦截 `TEMP_CROSS`；
- 两端端差相对差 ≤ 0.1% → 用算术平均 `(ΔT1+ΔT2)/2`，标记 `flat`；
- 工艺侧单侧温差 ≤ 5 ℃ → 警告近等温，纯显热 LMTD 不可靠；
- 实际传热温差 `ΔTm,eff = ΔTm · F`，F 未取得时取 1 并标 `F_UNCOVERED`。

Aspen `HX_DTLM` 只作对照列，不参与自算；两者差异大时要检查是否一侧为相变或 Aspen 已做 F 修正。

### 3.2 热负荷与反算 U

```text
U_back = |Q| / (A_aspen · ΔTm,self · F)
```

`U_back` 是「Aspen 简捷面积对应的等效 U」，不是 rating 结果。若 Aspen 简捷设计里人工填了 U（本项目实测为 850 W/(m²·K)），反算值会恰好落回该数，这是正常的自洽，不是独立验证。

### 3.3 需求面积与裕量

```text
A_req = |Q| / (U_ref · ΔTm,self · F) · (1 + margin)
```

- `U_ref` 优先级：人工选定的经验 U > 经验区间 default；
- `margin` 默认 0.30，可在抽屉里改；只影响 `A_req`，不改 Aspen 原面积；
- 页面「面积」列显示 `area.used_m2`（Aspen 面积优先，缺则人工面积），并标来源；`A_req` 单列对照。

### 3.4 设计压力

```text
P_design = max(1.1 · P_op, P_op + 1 bar)
```

管程、壳程分别按各自侧的操作压力算；结果保留 2 位小数。人工可在抽屉里覆盖，覆盖值优先并保留计算值对照。

### 3.5 设计温度

```text
T_design = T_max + ΔT_offset
```

`ΔT_offset` 默认 20 ℃，允许 15–30 ℃；超出范围回退 20 ℃。管程、壳程分别取各自侧最高温度。

### 3.6 允许压降

```text
气相：ΔP_allow = max(0.10 · P_op, 0.2 bar)
液相：ΔP_allow = max(0.20 · P_op, 0.2 bar)
```

这是 L1 的工程量级判据，不是 EDR 的详细压降预算；L2 才做管/壳程压降计算与三态校核。

### 3.7 污垢热阻

污垢档来自 `data/config/hx-fouling.json`（没有用户文件时用内置表）。内置 7 档：冷却水、处理水、河水、海水、有机液体、蒸汽、气体等。界面选择档位后同时落 `fouling_tube_key` 与数值 `fouling_tube`，导出时两者都可追溯。

## 4 五态判定与拦截规则

判定优先级：**blocked > unknown > fail > reference > pass**。L1 自身只产出前三种（pass / fail 需要 L2 几何）；L2 打开详情时才算后两种，并写回「结果与提示」页。

| 判定 | 含义 | 典型触发 | 界面徽标 |
| --- | --- | --- | --- |
| `blocked` | 边界外，不给 LMTD / 面积结论 | 工艺侧相变、温度交叉、多股换热、电加热公用工程、公用工程无温度 | 已拦截 |
| `unknown` | 缺关键输入，暂时算不出 | 热负荷缺、工艺侧温度不全、面积缺且需求面积也算不出、L2 缺物性 | 缺数据 |
| `reference` | 有完整输入，但含假设，只能参考 | Aspen 简捷面积、经验 U、预设/手填公用工程、F 未修正、cp 反推、标准表非 ok、人工几何 | 参考 |
| `pass` | L2 简捷校核通过 | 两侧压降不超允许、面积覆盖需求、无任何假设输入 | 简捷通过 |
| `fail` | L2 简捷校核不通过 | 管程或壳程压降超允许、标准面积小于需求面积 | 不合格 |

**「含假设最高只给 reference」是硬约束**：只要这台设备带任意一条假设（见 12.8），压降与面积都合格也只判 `reference`；`pass` 只在全部输入为实测且几何来自 ok 标准表时才出现。

拦截代码与中文提示（界面、导出共用）：

```text
PHASE_CHANGE            工艺侧进出口汽化率变化 → 转 EDR 分段
NEAR_ISOTHERMAL_PROCESS 工艺侧近等温 → LMTD 退化
TEMP_CROSS              逆流端差为负或近零
MISSING_PROCESS_TEMPS   工艺侧进出口温度不全
MISSING_DUTY            设备热负荷缺失
MULTI_STREAM            MHeatX / 多端口
UTILITY_NO_TEMP         公用工程无实测且未选预设/手填
UTILITY_ELECTRIC        电加热/燃料公用工程无流体温度
NO_SIDES                未取到冷热两侧
TUBE_FLUID_ILLEGAL      管程侧分配与设备类型不符
```

**相变只拦工艺侧。** Heater 用饱和蒸汽、制冷剂等**等温相变公用工程**不拦截：按等温端差处理并标 `UTILITY_ISOTHERMAL`；只有工艺侧自身发生相变才转 EDR。

**管程分配 7 条规则**（`data.py::TUBE_FLUID_RULES`）按顺序命中，规则值只作建议并标 `TUBE_FLUID_RULE`；用户在抽屉里改过后 `tube_fluid_source = user`，不再被规则覆盖。HeatX 只允许 hot/cold，Heater 只允许 process/utility，越界直接 400。

## 5 溯源标签

### 5.1 四项来源

| 项 | 取值 | 含义 |
| --- | --- | --- |
| 面积 | `aspen` | Aspen 简捷面积，由假设 U 推算 |
| 面积 | `manual` | 界面人工输入 |
| 面积 | `edr` | EDR 回填（Phase 2） |
| 面积 | `none` | 未取得 |
| 公用工程 | `aspen` | 公用工程节点实测温度 |
| 公用工程 | `preset` | 默认参数库预设 |
| 公用工程 | `manual` | 界面手填 |
| 公用工程 | `missing` | 都没有，拦截 |
| U | `aspen` / `assumed` / `reference` | Aspen 设计值 / 经验值 / 经验区间 |
| LMTD | `computed` / `aspen` | 自算 / 直接取 Aspen |
| 物性 cp | `aspen` / `derived` / `missing` | 流股节点读到 / 由 `Q/(m·ΔT)` 反推 / 缺失 |
| 物性 λ、μ | `aspen` / `missing` | 流股节点读到 / 缺失（λ 无兜底算法） |

### 5.2 页面与导出的显示口径

- 抽屉左栏每行右侧写来源，不把假设值写成校核值；
- 主表「状态 / 来源」列显示徽标 + `Aspen简捷 · 公用工程实测` 这类简写；
- Word / Excel 的「数据来源」列写 `面积:Aspen简捷 · 公用工程实测`；
- AI 包 CSV 用英文字段 `area_source / utility_source / verdict`，空值留空。

## 6 型号编码

按 GB/T 151 格式：

```text
<前端><壳体><后端><DN>-<Pt>/<Ps>-<A>-<L>/<d>-<N><级别>
```

- 前端默认 B（封头）、壳体 E、后端 M、级别 I；可在 `hx_selection` 覆盖；
- 压力 bar → MPa，保留 3 位并去尾零；
- 缺 DN / 公称面积 / 管长 / 管径 / 管程数时输出 `?` 占位，`missing[]` 列出缺项；
- 枚举非法（如前端写 X）直接报错，不静默纠正。

因此表里看到 `BEU?` 这类带 `?` 的编码，含义是「编码框架已生成、几何未补齐」，不是错编码。

## 7 输出

### 7.1 换热器选型一览表 15 列

Word《换热器选型一览表》严格按方案 §9.2 冻结顺序：

```text
位号 | 设备名称 | 型号 | 公称面积 m² | 管数 | 管长 m | 管径 mm | 管程数 |
壳径 mm | 壳体材质 | 管束材质 | 台数 | 执行标准 | 数据来源 | 备注
```

空值统一 `—`，不补 0；表尾汇总「假设/未确认项数量、已拦截台数、缺数据台数、标准表状态」。若已导入 GB/T 28712.2 标准表且该台有 L2 推荐行，型号与公称面积、管数、管长、管径、管程、壳径列回退填 L2 推荐几何，并在备注写「L2 推荐」；人工覆盖优先，不会被推荐值改写。标准表状态为 `missing` 时表尾写「未导入标准表，未做标准系列匹配」。

### 7.2 整本 Excel「换热器」页

15 列 + 「判定」「缺失项」两列，数值留空而不是 `—`，便于筛选与二次计算。

### 7.3 AI 包 `heat_exchangers.csv`

英文字段直接给 AI 或脚本读，含 `area_source / utility_source / u_source / verdict / missing / blockers / warnings`；同包附 `hx_prompt.md`，说明「reference 不等于合格、空值不是 0、下一步要 EDR rating」。

### 7.4 EDR 输入清单（三页签）

`GET /api/cases/{id}/heat-edr.xlsx` 输出三张录入页 + 一张说明页，一台设备在每页各占一行（工艺页按冷热两侧各一行）：

| 页签 | 内容 | 用途 |
| --- | --- | --- |
| `EDR工艺输入` | 位号、侧别、进出口温度、流量、体积流量、汽化率、操作压力、热负荷、ΔTm、F、污垢、允许压降、物性（μ / cp / λ）与物性来源 | 交给 EDR 建 case |
| `EDR结构输入` | 标准行键、壳径、管径、壁厚、管长、管心距、管数、管程、排列、公称面积、几何来源、两侧接管 | 对齐选定的标准系列 |
| `结果与提示` | A_req、A_std、面积比、U 计算、两侧 h、两侧 Δp、判定、假设、备注 | 复核与交接 |
| `说明` | 单位、空值含义、四项来源、边界 | 阅读说明 |

第 2 行是灰字单位提示（不合并单元格），`A3` 冻结、表头带筛选。它是**录入清单**，不是 rating 结果；`pass` 也不代表 EDR 会通过。

## 8 人工决定与默认参数库

抽屉右栏可改：管程侧、管程/壳程污垢档、面积裕量、F 修正、公用工程预设或手填温度，以及保存后的设计条件覆盖。

「默认参数库」弹窗分三个 Tab：

1. **污垢热阻**：键名 + 数值（m²·K/W）；
2. **经验 U 区间**：min / default / max（W/(m²·K)），default 必须落在区间内；
3. **公用工程预设**：名称、进出口温度、相态（condensing / boiling / liquid / gas）。

保存写入 `data/config/hx-*.json`，只覆盖当前页；「恢复本页内置默认」删除覆盖文件并重载 `app/design/heat/data.py` 的内置值。内置值是只读兜底，程序不会主动生成用户文件。

## 9 边界与未实现

- 不做 EDR rating、折流板漏流 / 旁路修正、振动、SW6 强度；
- 不做两相分段、空冷器、板式、螺旋板；L2 水力只对单相；
- `HX_DTLM` 是否已含 F 修正未验证，样本 F=1 无法区分，页面标「是否含 F 未验证」；
- 物性只读 Aspen 流股已挂物性集的结果：默认模型只有 `MUMX`，`CP` / `THK` 需要用户在 Aspen 里挂物性集后才有；读不到就留空，`cp` 可反推、`λ` 不猜（见 12.1）；
- 无 `UTILITY_ID` 的 Heater（本项目 B3、E0403）按缺公用工程处理，不猜；
- 标准系列表不在仓库内分发，需用户按模板自行导入；本地仅录入 φ19 / φ25 部分 DN 档，覆盖不足时按缺表降级处理（见 12.3、12.4）。

## 10 复算算例（V15 实测 EX0101）

数据来自本机 Aspen Plus V15 全流程模型的换热器 EX0101 简捷结果（本机探针记录）：

| 量 | 实测值 |
| --- | --- |
| 热负荷 Q | 1183.95 kW |
| Aspen 面积 A | 26.6351 m² |
| Aspen UA | 5407.4368 cal/(s·K) |
| ΔTm(Aspen) | 52.2949 ℃ |
| F | 1.0 |
| HX_UAVD | 0.0203019 cal/(s·cm²·K) |

**步骤 1：UA 换算**

```text
UA = 5407.4368 × 4.1868 = 22639.86 W/K
U_design = 0.0203019 × 41868 = 850.0 W/(m²·K)
```

**步骤 2：反算 U**

```text
Q = 1183.95 kW = 1 183 950 W
U_back = 1 183 950 / (26.6351 × 52.2949 × 1.0) = 850.0 W/(m²·K)
```

反算值正好等于 Aspen 输入的设计 U，说明该面积就是按 850 W/(m²·K) 的简捷假设算出的，不能当成独立的 rating 校核。

**步骤 3：经验 U 下的需求面积**

若取经验 U = 850 W/(m²·K)、裕量 0.30：

```text
A_req = 1 183 950 / (850 × 52.2949 × 1.0) × 1.30 = 34.63 m²
```

即同样工况下按 30% 裕量需要 34.63 m²，而 Aspen 原面积 26.6351 m² 对应 0% 裕量。两者并列展示，不互相替代。

**步骤 4：设计条件（示例操作压力 10 bar 绝）**

```text
P_design = max(1.1 × 10, 10 + 1) = 11.0 bar
T_design = T_max + 20
ΔP_allow(液相) = max(0.2 × 10, 0.2) = 2.0 bar
```

**步骤 5：判定**

面积为 `aspen`、U 为经验值、F 未修正 → 判定只能是 `reference`；备注列同时写「面积来自 Aspen 简捷假设」与「F 未修正」。

## 11 常数与默认表

**表 11-1 内置默认（`app/design/heat/data.py`）**

| 表 | 档数 | 内容 |
| --- | --- | --- |
| 污垢热阻 | 7 | 冷却水、处理水、河水、海水、有机液体、蒸汽、气体 |
| 经验 U | 7 | 水-水、水-有机、气-气、冷凝器、再沸器等区间 |
| 公用工程预设 | 8 | 低压蒸汽、中压蒸汽、高压蒸汽、冷却水、冷冻水、盐水、导热油、空气 |
| 管程分配规则 | 7 | 按介质、压力、相态、污垢倾向顺序命中 |

**表 11-2 判据常数**

| 常数 | 值 | 用途 |
| --- | --- | --- |
| `DESIGN_P_FACTOR` | 1.1 | 设计压力系数 |
| `DESIGN_P_MARGIN_BAR` | 1.0 bar | 设计压力附加 |
| `DESIGN_T_DEFAULT_OFFSET` | 20 ℃ | 设计温度附加值（15–30） |
| `DP_ALLOW_GAS_RATIO` | 0.10 | 气相允许压降比例 |
| `DP_ALLOW_LIQUID_RATIO` | 0.20 | 液相允许压降比例 |
| `DP_ALLOW_FLOOR_BAR` | 0.2 bar | 允许压降下限 |
| `PHASE_EPS` | 1e-4 | 汽化率变化判相变 |
| `TEMP_CROSS_DT` | 1.0 ℃ | 温度交叉判据 |
| `UTILITY_ISOTHERMAL_DT` | 1.0 ℃ | 公用工程等温相变判据 |

## 12 L2 几何初选

L2 在打开单台换热器抽屉时按需计算：先拿 L1 结果（热负荷、两侧温度、ΔTm、A_req、允许压降），再读这台设备四端流股的物性，最后在标准系列表上枚举 / 判定。所有计算都能在离线自测（`tests/test_hx_l2.py`）里复现，不依赖 Aspen。

### 12.1 物性读取与缓存

物性只从 Aspen 流股的 `Output` 节点读，按候选路径逐个试，再退化为扫描 `STRM_UPP` 子节点名（兼容用户自定义物性集名）：

```text
\Data\Streams\<S>\Output\STRM_UPP\CP     比热
\Data\Streams\<S>\Output\STRM_UPP\THK    导热系数
\Data\Streams\<S>\Output\STRM_UPP\MUMX   动力粘度
```

- 读到 `CP` / `THK` 是标量直接取；是节点则按 `MIXED → TOTAL` 下钻取第一个标量叶子，多条子流股只取第一条并在结果里标 `partial`；
- 单位经 `units.convert()` 统一到 cp = J/(kg·K)、λ = W/(m·K)、μ = cP；**比热只收质量基**，`cal/mol-K` 这类摩尔基直接判为缺口，不拿分子量猜；
- 认不出的单位写进 `unit_gaps`（如 `cp:cal/mol-k`），页面提示「Aspen 单位未识别，请在 Aspen 里改成 J/kg-K」；
- 只对当前这台设备的冷热两侧流股读（4 条），不扩全表；结果缓存到 `hx_props` 表（PK `case_id + stream`），下次打开直接命中；
- **缓存只存 Aspen 真读到的值**，反推出来的 `cp` 不落库，免得下次被当成实测；
- 读不到不报错：`props` 里对应项留空并把 `source` 标 `missing`，L2 判 `unknown` 而不是 500。

### 12.2 cp 由热负荷反推

缺 `cp` 但热负荷、流量、单侧温差都可用时：

```text
cp = |Q| / ((m/3600) · |ΔT|)
```

门槛：`|Q| ≥ 0.5 kW`、`|ΔT| ≥ 2 K`、进出口汽化率都 ≤ 0.02（单相）。满足才反推，并标 `source.cp = derived`、备注「仅作校核值」，同时进入 L2 假设清单——**有 derived 的设备最高只能判 reference**。λ 没有同量级兜底公式，缺失即缺失。

### 12.3 标准系列表与版本

表来自 GB/T 28712.2-2023 的固定管板式系列，CSV 必需列：

```text
DN, shell_id_mm, tube_od_mm, tube_wall_mm, tube_length_mm, tube_pitch_mm,
tube_passes, tube_count, area_m2, arrangement
```

导入校验（任一条不过整批拒绝，报错带行号）：

- 表头缺列、UTF-8 解不开、文件 > 8 MB → 拒；
- `DN` 150–2000 mm、壁厚 1–5 mm、管长 1000–9000 mm、管程 ∈ {1, 2, 4, 6}、管数 1–5000；
- 管心距 / 外径 ∈ 1.20–1.70（太小无法布管，太大浪费壳径）；
- 面积自洽，按标准 §5.7 口径：

```text
A1 = n · π · d_o · (L − 2δ − 2L1)
δ = 50 mm (DN ≤ 2400)，L1 = 3 mm
```

计算值与 `area_m2` 相对偏差 > 3% 判串列（这是 PDF 抄表最常见的错位）；`area_m2` 必须 > 0；同一 `DN|d_o|pitch|管程|管长` 只允许一行。

状态门槛：有效行 ≥ 20、DN ≥ 3 档、管径 ≥ 2 档 → `ok`；行数够但档数不够 → `partial`；没有文件 → `missing`；文件在但一行都过不了 → `invalid`。只有 `ok` 才允许在无假设时判 `pass`。

版本化：每次导入写入 `data/preview/hx-std/versions/<时间戳>/`，成功后用 `os.replace` 原子切换 `active.txt`；新版本先在 staging 试加载，失败就把指针切回上一版，不会出现「导入一半」。仓库只带 `samples/hx-standards-template/` 的空模板（表头 + 2 行示例，`area_m2` 故意写错），标准数值由用户自行导入；模板原样导入会按文本指纹拒绝。

「复制填写提示词」把表头、示例行、单位（MPa→bar）、A1 口径、20 行 / 3 DN / 2 管径门槛一次给全，可直接粘给 AI 或同事填表。

### 12.4 缺表降级

未导入标准表、或状态不是 `ok` 时**不阻塞 L1**，L2 输出：

- `std_skipped = true`，附 `reason`（缺文件 / 行数不足 / 档数不足 / 全表串列）；
- 理论需求面积 `A_req`（来自 L1，含裕量）；
- 经验推荐范围：19 / 25 mm 管径 × 4500 / 6000 / 7500 mm 管长，按「管数 × 单管面积」反算管数区间，再用 Kern 管束直径估算壳径，给「最小管数 – 推荐管数」区间；
- 前端画黄条「未导入标准表，以下为经验范围」，不列标准行候选。

因此缺表时页面仍有可读结论，但不会有型号与标准面积。

### 12.5 几何枚举与管程水力

对每条标准行（`n` 根、`Np` 程、`d_i = d_o − 2δ`）：

```text
A_flow = (n / Np) · π · d_i² / 4
v      = Q_v / A_flow
Re     = 4 · (m/3600) · Np / (n · π · d_i · μ)
Pr     = μ · c_p / (1000 · λ)
Nu     = 0.023 · Re^0.8 · Pr^n     （被加热 n=0.4，被冷却 n=0.3）
h      = Nu · λ / d_i
```

- 管程压降复用 `app/design/hydraulics`：Darcy 摩擦因子按 Re 分区（层流 64/Re、过渡区插值、湍流 Colebrook），粗糙度 0.03 mm；
- 局部损失系数 `ζ = 1.5（进口）+ 1.0（出口）+ 1.5×(Np−1)（每程回弯）`；
- `Δp = ρ · g · Σh_f / 1e5`（bar），ρ 由质量流量 / 体积流量得到；
- 流速落在 0.5–1.5 m/s 之外只告警（`TUBE_VELOCITY`），不判失败；`h` 落在 20–20000 W/(m²·K) 之外告警（`H_OUT_OF_RANGE`），提示物性或流量可疑；
- 缺 μ / cp / λ 时对应项为空并把缺项写进 `missing`，由判定层转 `unknown`。

### 12.6 壳程 Kern 估算

```text
A_c = D_s · (P − d_o) · B / P
G   = (m/3600) / A_c
d_e = 1.10/d_o · (P² − 0.917·d_o²)          正三角形
d_e = 4(P² − π·d_o²/4) / (π·d_o)             正方形
Re  = d_e · G / μ
Nu  = 0.36 · Re^0.55 · Pr^(1/3)
h   = Nu · λ / d_e
f   = exp(0.576 − 0.19·ln Re)
N_b = round(L / B) − 1
Δp  = f · G² · (N_b + 1) · D_s / (2 · ρ · d_e)
```

折流板间距 `B = 0.30·D_s`，夹到 `0.2–1.0·D_s` 并取整到 10 mm。Kern 是平均口径：不含折流板缺口漏流、旁路与进出口段修正，所以壳程 `h` 偏保守、`Δp` 偏差可能到 ±30%，只作量级判断。

### 12.7 接管圆整

```text
d_req = sqrt(4 · Q_v / (3600 · π · v))
```

`v` 取液相 1.5 m/s、气相 15 m/s。得到需求内径后在 GB/T 17395 钢管规格里找**内径 ≥ d_req 的最小外径**，同一外径取最薄壁；钢管标准表缺失时返回「未圆整」，页面注明接管仅为估算。接管尺寸只进结构输入，不参与压降与判定（`NOZZLE_ESTIMATED` 只作提示）。

### 12.8 总传热系数与五态判定

```text
1/U_dirty = 1/h_tube + R_tube + R_shell + 1/h_shell
1/U_clean = 1/h_tube + 1/h_shell
A_need = |Q| / (U_dirty · ΔTm · F)
```

判定顺序（`rating.judge()`）：

1. L1 已 `blocked` → 直接 `blocked`，不重算；
2. 缺 Q / ΔTm / 任一侧 `h` / `U` → `unknown`，并列出缺项；
3. 压降超允许（管程或壳程任一）→ `fail`；`A_std / A_req < 0.98`（标准面积小于需求面积）或 `A_need / A_std > 1.05`（真实需求面积超过公称面积）→ `fail`；
4. 有任意假设 → `reference`；
5. 全部实测且几何来自 `ok` 标准表 → `pass`。

`A_std / A_req > 1.30` 只提示「面积偏大、可考虑更短管长」，不算失败；几何算出的 `U` 与 L1 反算 `U` 相差 > 30% 只告警 `U_MISMATCH`（通常说明 Aspen 简捷里填的 U 是经验值，与几何不自洽）。

**假设清单**（任一条即封顶 reference）：Aspen 简捷面积、F 未修正、公用工程预设 / 手填、管程分配按规则建议、ΔTm 直接取 Aspen、cp 为反推值、标准表非 ok、几何为人工覆盖。

### 12.9 三页签 EDR 与人工覆盖

- 抽屉 L2 面板列出候选（标准行键、A_std、面积比、U 计算、两侧 Δp、判定徽标），点「采用」把 `std_row_key` 与 `geom_*` 写进 `hx_design`，`geom_source = manual`；
- 人工覆盖后判定仍按同一套阈值算，只是假设清单多一条「几何为人工覆盖」，所以最多 `reference`；「清除」把 `geom_*` 置空，回到推荐行；
- 未导入标准表时面板顶部显示黄色横幅，只给 A_req 与经验推荐范围；
- 导出三页签见 7.4；Word 一览表与整本 Excel 的几何列在无人工覆盖时回退 L2 推荐值，备注标「L2 推荐」。

## 附录 A　核心代码函数映射表

| 函数 | 位置 | 用途 |
| --- | --- | --- |
| `lmtd` | `app/design/heat/params.py` | 逆流 LMTD 与 flat / cross |
| `design_pressure` | 同上 | 设计压力 |
| `design_temperature` | 同上 | 设计温度 |
| `allowable_dp` | 同上 | 允许压降 |
| `build_design` | 同上 | L1 主流程与判定 |
| `build_code` | `app/design/heat/model_code.py` | GB/T 151 编码 |
| `build_row` / `assemble` | `app/design/heat/assemble.py` | 页面与导出共用拼行 |
| `read_utilities` | `app/aspen/reader.py` | 公用工程节点温度 |
| `read_stream_hx_props` / `read_hx_props` | 同上 | 四端流股 cp / λ / μ（只读本台） |
| `get` | `app/api/hx_props.py` | 物性按需读 + 缓存 + 降级 |
| `parse_series` / `import_standards` | `app/design/heat/standards.py` | 标准表校验与版本化导入 |
| `estimate_ranges` | 同上 | 缺表时的经验推荐范围 |
| `standards_prompt` / `edr_prompt` | `app/design/heat/prompt.py` | 填写提示词 |
| `cp_from_duty` / `side_props` | `app/design/heat/params.py` | cp 反推与两侧物性来源 |
| `candidates` / `tube_side_flow` / `shell_side_flow` | `app/design/heat/geometry.py` | 几何枚举与两侧水力 |
| `nozzle_size` / `round_nozzle` | 同上 | 接管估算与 GB/T 17395 圆整 |
| `judge` / `select` / `evaluate` | `app/design/heat/rating.py` | 五态判定、排序与一步求值 |
| `build_edr_checklist` | `app/export/heat.py` | 三页签 EDR xlsx |
| `build` | `app/export/hx_schedule.py` | 一览表（L2 几何回退 + 判定汇总） |
| `load_config` / `save_config` / `reset_config` | `app/store/heat_config.py` | 默认参数库读写与恢复 |

## 附录 B　版本记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v1.7.5 | 2026-11-23 | 首版：L1 公式、五态判定、溯源标签、15 列导出与 V15 复算算例 |
| v1.8.0 | 2026-11-23 | Phase 2（L2）：物性按需读取与 cp 反推、GB/T 28712.2 标准表导入与缺表降级、几何枚举与管/壳水力、接管圆整、五态判定、三页签 EDR |

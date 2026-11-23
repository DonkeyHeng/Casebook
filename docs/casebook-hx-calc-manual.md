# 换热器选型计算说明书

**Casebook · 计算说明书**

- 专业：换热专业 · 计算册
- 版本 v1.7.5 · 2026-11-23 · Casebook
- 说明：本说明书解释 L1 算法口径，不替代 EDR rating、GB/T 151 强度校核与工程设计审核

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
  - 7.4 EDR 输入清单
- 8 人工决定与默认参数库
- 9 边界与未实现
- 10 复算算例（V15 实测 EX0101）
- 11 常数与默认表
- 附录 A　核心代码函数映射表
- 附录 B　版本记录

## 1 适用范围与计算链

本说明书描述 Casebook「换热」页对一台 Aspen 换热类设备（HeatX / MHeatX / Heater / HeaterX）做 L1 选型整理的完整算法：读哪些字段、按什么顺序算、哪些情形不计算、每个结果标什么来源。

L1 只做四件事：

1. **整理工况**：从连接流股与公用工程节点取冷热两侧进出口温度、热负荷、汽化率；
2. **自算 LMTD**：逆流对数平均温差，允许 F 修正但 F 默认 1；
3. **反算 U 与需求面积**：用 Aspen 简捷面积反算 U，再按经验 U 与裕量算需求面积；
4. **给设计条件与编码**：设计压力、设计温度、允许压降、污垢热阻、GB/T 151 型号编码。

L1 **不做**：EDR rating、h/Δp 详细计算、振动分析、SW6 强度校核、两相分段、空冷器、板式换热器。因此 Phase 1 的判定上限是「参考」，不输出「合格 / 不合格」。

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

判定优先级：**blocked > unknown > reference**；Phase 1 不产出 pass / fail。

| 判定 | 含义 | 典型触发 | 界面徽标 |
| --- | --- | --- | --- |
| `blocked` | 边界外，不给 LMTD / 面积结论 | 工艺侧相变、温度交叉、多股换热、电加热公用工程、公用工程无温度 | 已拦截 |
| `unknown` | 缺关键输入，暂时算不出 | 热负荷缺、工艺侧温度不全、面积缺且需求面积也算不出 | 缺数据 |
| `reference` | 有完整输入，但含假设，只能参考 | Aspen 简捷面积、经验 U、预设/手填公用工程、F 未修正 | 参考 |
| `pass` | 简捷校核通过 | Phase 1 不产出 | 简捷通过 |
| `fail` | 简捷校核不通过 | Phase 1 不产出 | 不合格 |

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

空值统一 `—`，不补 0；表尾汇总「假设/未确认项数量、已拦截台数、缺数据台数、标准表状态」。GB/T 28712.2 标准系列表在 L2 才接入，表尾明确写「未启用，不做标准系列匹配」。

### 7.2 整本 Excel「换热器」页

15 列 + 「判定」「缺失项」两列，数值留空而不是 `—`，便于筛选与二次计算。

### 7.3 AI 包 `heat_exchangers.csv`

英文字段直接给 AI 或脚本读，含 `area_source / utility_source / u_source / verdict / missing / blockers / warnings`；同包附 `hx_prompt.md`，说明「reference 不等于合格、空值不是 0、下一步要 EDR rating」。

### 7.4 EDR 输入清单

`GET /api/cases/{id}/heat-edr.xlsx` 按冷热侧物性、允许压降、污垢、几何分组输出 40 列，第二页「说明」写单位、空值含义、四项来源与边界；它是**录入清单**，不是 rating 结果。

## 8 人工决定与默认参数库

抽屉右栏可改：管程侧、管程/壳程污垢档、面积裕量、F 修正、公用工程预设或手填温度，以及保存后的设计条件覆盖。

「默认参数库」弹窗分三个 Tab：

1. **污垢热阻**：键名 + 数值（m²·K/W）；
2. **经验 U 区间**：min / default / max（W/(m²·K)），default 必须落在区间内；
3. **公用工程预设**：名称、进出口温度、相态（condensing / boiling / liquid / gas）。

保存写入 `data/config/hx-*.json`，只覆盖当前页；「恢复本页内置默认」删除覆盖文件并重载 `app/design/heat/data.py` 的内置值。内置值是只读兜底，程序不会主动生成用户文件。

## 9 边界与未实现

- 不做 EDR rating、h/Δp、振动、SW6 强度；
- 不做两相分段、空冷器、板式、螺旋板；
- `HX_DTLM` 是否已含 F 修正未验证，样本 F=1 无法区分，页面标「是否含 F 未验证」；
- 物性集（CP / 导热系数 / 粘度 / 密度）未读取，Phase 2 需模型挂物性集后补；
- 无 `UTILITY_ID` 的 Heater（本项目 B3、E0403）按缺公用工程处理，不猜。

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
| `load_config` / `save_config` / `reset_config` | `app/store/heat_config.py` | 默认参数库读写与恢复 |

## 附录 B　版本记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v1.7.5 | 2026-11-23 | 首版：L1 公式、五态判定、溯源标签、15 列导出与 V15 复算算例 |

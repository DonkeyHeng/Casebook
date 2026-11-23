# 换热器选型功能方案（L1 工艺条件 · L2 几何初选）

> 版本：2026-11-23（方案待评审，尚未改动业务代码）
> 适用范围：管壳式换热器（以固定管板 BEM 为主），数据源为 Aspen Plus 只读读数 + 人工输入 + 可外置的标准系列表。
> 依据：三份公开课程设计文档的换热器章节（仅提取通用规则，不引用具体项目数据）；本机库 `data/cases.db`（6 个工况）与示例库 `samples/case-23-error.db` 的实测字段覆盖。
> 关联文档：[`aspen-field-map.md`](aspen-field-map.md)（字段路径）、[`pump-selection-plan.md`](pump-selection-plan.md)（选型台账与三态判定的既有范式）、[`pipe-selection-plan.md`](pipe-selection-plan.md)（标准表外置与降级范式）、[`../app/README.md`](../app/README.md)（分层与依赖方向）。

---

## 0. 一页结论

| 项目 | 结论 |
| --- | --- |
| 能不能做 | **能**：L1（工艺条件、LMTD/反算 U、设计温压、允许压降、污垢热阻、GB/T 151 编码、EDR 输入清单）与 L2（标准系列枚举、管程/壳程压降估算、三态校核）都可落地 |
| 不能做什么 | **不做** EDR 级 Rating（Bell-Delaware 全修正、振动、两相分区）、不做 SW6 机械强度、不做空冷器/板式/螺旋板、不写回 Aspen |
| 最大数据缺口 | Heater（占本机库换热设备的 112/140）没有面积、没有冷侧流股；两侧 `cp` 与导热系数 `λ` 未采集；GB/T 28712.2 系列表不在仓库 |
| 首期能否立刻验证 | 能：历史 6 个工况共 28 台 HeatX 全部有面积与四端温度；本机 V15 模型的 4 台可与 `HX_DTLM`、`HX_UAVD`、`HX_UA` 三方交叉校核 |
| 分期 | Phase 1 完整闭环（L1）；Phase 2 几何初选（L2）。Phase 1 即建全部表与字段，Phase 2 只填内容，不重构 |
| 结论口径 | 含任一假设（公用工程预设、人工 U、人工面积、未含 F 修正）时，判定只能是「参考」，**不得**出现「校核合格」字样 |

---

## 1. 功能边界

### 1.1 做

1. **把换热设备读数整理成可直接用于选型的条件表**：冷/热/公用工程侧的温度、压力、流量、相态、组成（按需）、热负荷、面积。
2. **算 LMTD、反算 U、UA 交叉校核**，并对面积来源与假设做显式标注。
3. **按规则给设计压力、设计温度、允许压降、污垢热阻建议值**，允许逐台覆盖。
4. **按 GB/T 151-2014 生成型号编码**（缺项显式留空，不脑补）。
5. **L2：在导入标准系列表后做标准规格枚举与初选**，给出管程/壳程压降与总传热系数的量级估算，输出三态/五态校核。
6. **导出**：《换热器选型一览表》Word、EDR 输入参数清单 Excel、整本 Excel 的换热页、AI 分析包 `hx.csv`、计算说明书 Word。

### 1.2 不做（写死，防止越界）

| 不做 | 理由 |
| --- | --- |
| EDR 级 Rating / Checking | 三份文件均把 h、Δp、振动交给 EDR，正文没有可复现的计算式；自研无法对标，做了也会被误当成校核结论 |
| 机械强度（筒体/封头/管板/法兰/开孔补强） | 属 SW6 与 GB/T 150.3 范畴；只做一半（比如只算筒体）反而制造「已校核」的错觉 |
| 两相分区 / 冷凝 / 沸腾的严格计算 | 需要分区焓线与相变关联式；第一版显式拦截，引导转 EDR |
| 空冷器、板式、螺旋板、套管式 | 第一版只做管壳式；空冷器另有一套翅片与风机口径 |
| 自动生成工艺条件图 / CAD | 无可靠结构化来源，且不属于选型计算 |
| 换热网络优化 / 经济性比选 | 属 Aspen Energy Analyzer 范畴，本工具只读不算 |
| 写回 Aspen（Run / Reinit / Save） | 与项目「无侵入只读」原则冲突 |

### 1.3 第一版型式边界

- 结构：**管壳式**；主推 **BEM**（固定管板、E 型壳体、M 型后端）。
- 编码器覆盖常见组合：前端 `A/B/C`，壳体 `E/F/K`，后端 `M/S/U/T`；即 BEM、BEU、AES、BKU 等可编码。
- 不在上述组合内 → 编码器只返回分段与缺项，不拼一个「看起来像」的型号。
- TEMA 与 GB/T 151 的字母含义在个别位置不完全一致；本工具**以 GB/T 151-2014 为准**，UI 注明。

---

## 2. 数据从哪来（字段映射与实测覆盖）

### 2.1 已有读取（代码已在跑，无需新增）

| 量 | 来源 | 路径 | 单位 | 现状 |
| --- | --- | --- | --- | --- |
| 热负荷 | 块 `Output` | `HX_DUTY`（HeatX）；`QCALC` / `QNET`（Heater / HeaterX） | `cal/sec` 或 `MW`，按 `UnitString` 换算 | ✅ `reader.BLOCK_DUTY_FIELDS` |
| 换热面积 | 块 `Output` | `HX_AREAC` → `HX_AREAC2` → `HX_AREAP`（前者常空） | `sqm` | ✅ `reader.BLOCK_AREA_FIELDS` |
| 四端温度 | 连接 + 流股 | `connections.side=hot/cold` → `streams.temperature_c` | ℃ | ✅ `enrich_side_temperatures()` |
| 压力 | 流股 | `RES_PRES` | bar | ✅ |
| 质量/体积流量 | 流股 | `RES_MASSFLOW` / `RES_VOLFLOW` | kg/h、m³/h | ✅ |
| 汽化率 | 流股 | `RES_VFRAC` | 无量纲 | ✅ |
| 密度 | 派生 | 质量流量 ÷ 体积流量（单相） | kg/m³ | ✅ 泵侧同一口径 |
| 组成 | 流股 | `X` / `Y` / `MASSFLOW3` | 摩尔分数、kg/h | ✅ 按需读 |
| 连接分侧 | 块 | `Input\FCOLD/FHOT`、`Output\COLDIN/COLDOUT/HOTIN/HOTOUT` | 流股名 | ✅ `read_connections()` |

### 2.2 Phase 1 新增读取（本方案第一件事，先探针后落地）

| 量 | 来源 | 路径（待探针确认） | 单位 | 用途 |
| --- | --- | --- | --- | --- |
| 公用工程名 | Heater/HeaterX 块 | `Input\UTILITY_ID`（如 `MP`） | 文本 | 把块与公用工程树对上 |
| 公用工程进出口温度 | 公用工程树 | `\Data\Utilities\<util>\Output\UTL_IN_TEMP` / `UTL_OUT_TEMP` | ℃ | Heater 的「另一侧」温度 → LMTD |
| 公用工程类型 | 公用工程树 | `\Data\Utilities\<util>\Input\UTILITY_TYPE` | 文本 | `STEAM` / `WATER` / `ELECTRICITY` / `GENERAL`，判能否作为等温侧 |
| 公用工程用量（可选） | 公用工程树 | `UTL_TRATE`（kg/h） | kg/hr | 仅展示 |
| Aspen 平均温差 | HeatX 块 | `HX_DTLM` | ℃ | 与自算 LMTD 交叉校核 |
| F 修正 | HeatX 块 | `HX_FMTD` | 无量纲 | 多管程 F（本机 4 台均 1.0） |
| 设计 U | HeatX 块 | `HX_UAVD` | `cal/sec-cm²-K` | 与 `U_back` 互校，直接暴露简捷假设 |
| 串联壳数 | HeatX 块 | `HX_NSHLS` | 个 | >1 时提示多壳串联 |
| UA 值 | 块 `Output` | `HX_UA` | `cal/sec-K` | 与 `U_back = Q/(A·ΔTm)` 互校 |

> **实测校正（2026-11-23）**：`UTL_DUTY` 在已投用的公用工程上读到的都是 `1 cal/sec`（是标志位，不是负荷），**不得用于任何计算**；热负荷只认块上的 `QCALC/HX_DUTY`。`UTL_IN_TEMP/OUT_TEMP` 只在「已配置且有设备引用」的公用工程上有值（本机 7 个里 3 个有值）。

> `aspen-field-map.md` §12.4 已实测过 7 个公用工程成员与四个温度/用量字段，但**代码目前完全没读**（`reader.py` 无 `UTILITY` 关键字）。这是 Phase 1 新增的**公用工程树**依赖；`HX_UA`、`HX_DTLM`、`HX_FMTD`、`HX_UAVD` 也在同一次改造里加读。**V15 已用 `tools/probe_hx.py` 跑通**（见 §2.4）；V11/V12.1 的叶子层数待用对应模型复核。

**公用工程预设库**（`data/config/hx-utility-presets.json`，可编辑；仅在读不到 `UTL_IN_TEMP/OUT_TEMP` 时启用）：

| 键 | 温度 ℃ | 相态 | 说明 |
| --- | --- | --- | --- |
| `ls-135` | 135 / 135 | condensing | 低压饱和蒸汽 |
| `ms-175` | 175 / 175 | condensing | 中压饱和蒸汽 |
| `hs-250` | 250 / 250 | condensing | 高压饱和蒸汽 |
| `cw-25-35` | 25 → 35 | liquid | 循环冷却水（常规） |
| `cw-30-40` | 30 → 40 | liquid | 循环冷却水（夏季） |
| `chilled-7-12` | 7 → 12 | liquid | 冷冻水 |
| `brine-m15-m5` | −15 → −5 | liquid | 冷冻盐水 |
| `air-25-35` | 25 → 35 | gas | 空冷 / 风机 |

- 这些是**预设假设**，不是实测：一旦采用，`utility_source=preset`，判定自动降为 `reference`，界面与导出均标注「预设」。
- 数值属工程常用档，允许用户改；改过的文件版本号写进 `hx_design` 的备注，避免两次选型用的不是同一套预设。

### 2.3 Phase 2 新增读取（物性，按需读、不扩全表）

| 量 | 候选路径（与 `VISC` 同构，**均未实测**） | 目标单位 | 缺失时 |
| --- | --- | --- | --- |
| 粘度 μ | `STRM_UPP\MUMX`（已有）、`STRM_UPP\VISC\MIXED\TOTAL` | cP | 手填（沿用泵页范式） |
| 定压比热 cp | `STRM_UPP\CP\MIXED\TOTAL`、`STRM_UPP\CPMASS` | J/(kg·K) | 由 `abs(Q)/(m·abs(ΔT))` 反推（仅单相、仅作校核值） |
| 导热系数 λ | `STRM_UPP\THK\MIXED\TOTAL`、`STRM_UPP\KTH` | W/(m·K) | 手填；无则不算 h，只给 L1 |

- 读取时机：**打开单台换热器抽屉时按需读**（4 条侧流股 × 3 个物性 ≈ 12 次 COM 调用），成功结果写入 `hx_props` 缓存（按 `case_id + stream`），不参与全表读取。
- 页面提供一次性设置指引（与 `p_v` 同样的「物性集五步」），并给出当前模型缺哪几个物性。
- 认不出的单位一律留空并计入「单位缺口」，不拿常见值顶替（沿用 `units.convert()` 的既有纪律）。

### 2.4 实测覆盖（本机 `model/full` 下的 V15 全流程模型，2026-11-23 探针；另核 6 个历史工况）

| 统计 | 数值 | 说明 |
| --- | --- | --- |
| 设备总数 / 换热类 | 77 / 23 | HeatX 4 + Heater 19 |
| HeatX 面积 | 4/4 | `HX_AREAC2 = HX_AREAP`：26.64 / 32.33 / 112.34 / 19.62 m² |
| HeatX UA | 4/4 | `HX_UA` = 5407.4 / 6563.1 / 22807.2 / 3983.9 `cal/sec-K` |
| HeatX 平均温差 | 4/4 | `HX_DTLM` = 52.295 / 22.587 / 54.056 / 109.709 ℃；与自算 52.3 一致 |
| HeatX 设计 U | 4/4 | `HX_UAVD` = 0.0203019 `cal/sec-cm²-K` = **850.0 W/(m²·K)**，4 台同值 |
| HeatX 模式 | 4/4 Shortcut | `HX_EX_AREA / HX_EX_U / HX_EX_Q / HX_ZO_*` 全为 0 → 非 EDR Rating |
| Heater 的 UTILITY_ID | 17/19 | 缺失 2 台：`B3`、`E0403`（无公用工程或未配置） |
| 公用工程温度 | 6/7 | `MS` 247/246、`HS` 350/349、`LS` 135/134、`WAT` 32/40、`LWAT` 7/12、`AIR` 35/40 ℃；仅 `ELE` 无温度（电加热） |
| 公用工程类型 | 7/7 | `STEAM`（MS/LS/HS）、`WATER`（WAT/LWAT）、`GENERAL`（AIR）、`ELECTRICITY`（ELE） |
| 侧流股 `CP/THK` | 0/2 样本 | `STRM_UPP` 只有 `PL / MUMX / PBUB`，没有 `CP` 与 `THK` |

> 2026-11-23 复核更正：早先探针报告写「`LS`/`HS` 被引用却无温度」，是路径少了一层 `Output` 导致的假空值。按 `\Data\Utilities\<U>\Output\UTL_IN_TEMP / UTL_OUT_TEMP` 重读，`HS` 与 `LS` 都有温度，且都落在 1 ℃ 内（等温相变）。**以本表为准**；`MISSING_UTILITY_T` 只用于真的读不到温度的情形（本模型仅 `ELE`）。

**关键实测结论（决定了溯源设计）**：`HX_UAVD` 4 台全为 850.0 W/(m²·K)，`U_back = Q/(A·ΔTm)` 也全为 850.0 —— 面积来自 Aspen **简捷设计里人工填的 U**，不是 rating 结果；`HX_EX_*` 全 0 可作为「Shortcut 模式」的可靠判据，比「U 恰好 850」的猜测更硬。

---

## 3. 数据模型

### 3.1 设计原则

1. **只算不存**：LMTD、`U_back`、几何候选、压降、判定结论一律按当前工况实时算，不落库（与泵台账同一纪律）。
2. **表只放人工决定与声明**：`hx_design` 放设计输入与覆盖项，`hx_selection` 放选型台账；`hx_props` 只做 Aspen 读数的缓存。
3. **按模型身份去重**：键统一用 `model_identity.key_of()`（内容哈希优先、文件名兜底）。注意现有 `pump_design` 用的是 `model_name`，`pump_selection` 用的是 `model_key`；新表**只学后者**，不再引入第三种键。
4. **Phase 1 一次建全字段**：L2 的几何列在 Phase 1 就建好（默认 NULL），Phase 2 只写值，不改表。

### 3.2 `hx_design`（按模型 + 位号）

```sql
-- 换热器设计输入（按模型身份 + 位号）。只存**人工决定 / 人工覆盖 / 溯源声明**；
-- 面积、LMTD、U、几何候选、压降与判定一律实时算，不落库。
-- 单位统一：压力 bar、温度 ℃、面积 m²、污垢热阻 m²·K/W、
--           U 为 W/(m²·K)、长度 mm（几何）、裕量为小数（0.40 = 40%）。
CREATE TABLE IF NOT EXISTS hx_design (
    model_key        TEXT    NOT NULL,   -- model_identity.key_of()
    block            TEXT    NOT NULL,

    -- ---- 公用工程侧（仅 Heater / HeaterX 用；HeatX 的冷侧是流股，不填这里）
    utility_id       TEXT,               -- Aspen 公用工程名，如 MP
    utility_mode     TEXT,               -- none / aspen / preset / manual
    utility_key      TEXT,               -- 预设库键，如 ls-135 / cw-25-35
    utility_t_in_c   REAL,
    utility_t_out_c  REAL,
    utility_phase    TEXT,               -- liquid / vapor / condensing / boiling / unknown
    tube_fluid       TEXT,               -- hot / cold / process / utility：哪一侧走管程
    tube_fluid_source TEXT,              -- user / rule（默认按 §5.12 规则建议）

    -- ---- 设计条件（NULL = 按 §5.4/§5.5 规则自动算；非 NULL = 人工覆盖）
    p_design_tube_bar  REAL,
    p_design_shell_bar REAL,
    t_design_tube_c    REAL,
    t_design_shell_c   REAL,
    dp_allow_tube_bar  REAL,
    dp_allow_shell_bar REAL,
    t_design_offset_c  REAL,             -- 默认 20；允许 15~30

    -- ---- 污垢与传热
    fouling_tube       REAL,             -- m²·K/W
    fouling_shell      REAL,
    fouling_tube_key   TEXT,             -- 查表键，保留来源可追溯
    fouling_shell_key  TEXT,
    u_assumed          REAL,             -- W/(m²·K)；仅在无面积或需估算 A_req 时用
    u_ref_key          TEXT,             -- 经验区间键，如 water-water
    margin             REAL,             -- 面积裕量，建议 0.30~0.50
    f_correction       REAL,             -- 多管程 F；NULL = 未修正（按纯逆流算）

    -- ---- 面积（人工声明时使用）
    area_source        TEXT,             -- aspen / manual / edr / none（见 §7）
    area_manual_m2     REAL,
    area_nominal_m2    REAL,             -- 标准公称面积（编码用；不等于 A_req）

    -- ---- L2 几何（Phase 1 建列，Phase 2 才写；NULL = 未选）
    geom_tube_od_mm    REAL,
    geom_tube_wall_mm  REAL,
    geom_tube_length_mm REAL,
    geom_tube_passes   INTEGER,
    geom_tube_pitch_mm REAL,
    geom_tube_count    INTEGER,
    geom_arrangement   TEXT,             -- triangular / rotated_triangular / square / rotated_square
    geom_shell_od_mm   REAL,
    geom_shell_id_mm   REAL,
    geom_baffle_type   TEXT,             -- single_segmental / double_segmental / ...
    geom_baffle_spacing_mm REAL,
    geom_baffle_cut    REAL,             -- 0.25 = 25%
    geom_baffle_orient TEXT,             -- horizontal / vertical
    geom_source        TEXT,             -- standard / estimate / manual / none
    std_row_key        TEXT,             -- 选中的标准行（DN|od|pitch|passes|L）

    -- ---- 接管人工覆盖（NULL = 按 §5.10 自动算并圆整）
    nozzle_tube_in_mm    REAL,
    nozzle_tube_out_mm   REAL,
    nozzle_shell_in_mm   REAL,
    nozzle_shell_out_mm  REAL

    -- ---- 编码与备注
    head_code          TEXT,             -- 前端管箱 A/B/C
    shell_code         TEXT,             -- 壳体 E/F/K
    rear_code          TEXT,             -- 后端 M/S/U/T
    bundle_grade       TEXT,             -- 管束级别 I / II
    note               TEXT,
    version            INTEGER NOT NULL DEFAULT 0,   -- 乐观锁，每次写入 +1
    updated_at         TEXT,
    PRIMARY KEY (model_key, block)
);
```

### 3.3 `hx_selection`（选型台账）

```sql
-- 只存人工选定的结果，不存任何计算值。换工况后，派生量自动刷新。
CREATE TABLE IF NOT EXISTS hx_selection (
    model_key      TEXT    NOT NULL,
    block          TEXT    NOT NULL,
    model_no       TEXT,               -- 完整型号（人工确认后才写）
    service_name   TEXT,               -- 设备名称，如「水合工段原料预热器」
    qty            INTEGER,            -- 台数
    shell_material TEXT,               -- 壳体材质
    tube_material  TEXT,               -- 管束材质
    std_code       TEXT,               -- 执行标准，默认 GB/T 151-2014
    note           TEXT,
    updated_at     TEXT,
    PRIMARY KEY (model_key, block)
);
```

### 3.4 `hx_props`（物性缓存，Phase 2 写、Phase 1 建）

```sql
-- 按工况缓存 Aspen 按需读到的物性；不是设计数据，不跨工况复用。
-- source: aspen / derived / manual；derived 仅表示「由 Q、m、ΔT 反推」。
CREATE TABLE IF NOT EXISTS hx_props (
    case_id     INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    stream      TEXT    NOT NULL,
    mu_cp       REAL,
    cp_jkgk     REAL,
    k_wmk       REAL,
    source      TEXT,
    updated_at  TEXT,
    PRIMARY KEY (case_id, stream)
);
```

### 3.5 `blocks` 新增列（迁移，P1-1 实测后定稿）

```sql
-- 老库升级：CREATE TABLE IF NOT EXISTS 不会给已有表加列，按既有 _migrate() 逐个补。
ALTER TABLE blocks ADD COLUMN util_id      TEXT;
ALTER TABLE blocks ADD COLUMN util_type    TEXT;   -- STEAM / WATER / ELECTRICITY / GENERAL
ALTER TABLE blocks ADD COLUMN util_t_in_c  REAL;
ALTER TABLE blocks ADD COLUMN util_t_out_c REAL;
ALTER TABLE blocks ADD COLUMN hx_dtlm_c    REAL;   -- Aspen HX_DTLM，交叉校核用
ALTER TABLE blocks ADD COLUMN hx_f_factor  REAL;   -- Aspen HX_FMTD，0 视为无
ALTER TABLE blocks ADD COLUMN hx_u_design_wm2k REAL; -- Aspen HX_UAVD 换算
ALTER TABLE blocks ADD COLUMN ua_wk      REAL;   -- Aspen HX_UA，W/K
ALTER TABLE blocks ADD COLUMN util_isothermal INTEGER; -- 1 等温相变 / 0 显热 / NULL 温度不全
ALTER TABLE blocks ADD COLUMN util_source TEXT;  -- aspen / missing / undefined
```

> 2026-11-23 定稿：实际迁移 10 列（上表全部）。`util_isothermal` 用 INTEGER 存 0/1/NULL，
> `util_source` 区分「节点读到 / 引用但无温度 / 没有引用」，是 μ 与预设降级的判据。

- 旧工况这些列为 NULL，页面显示「公用工程温度缺，需重读或手填」；**不静默补默认值**。
- `tools.repair_cases` 不负责补这些列（没有 Aspen 就没有数据），只在文档与提示条说明「需重新读取工况」。
- `hx_dtlm_c` / `hx_f_factor` / `hx_u_design_wm2k` 是 **Aspen 读数快照**，不是我们的计算结果；是否采用仍由计算层决定（§5.2/§5.3）。

### 3.6 不建的表（明确排除）

- 不建「每工况每台换热器的完整侧数据快照」表：侧数据可由 `streams + connections + blocks` 实时拼出，落库会造成同一根流股两个数。
- 不把标准系列表写进 SQLite：走文件 + 版本指针（见 §8），与管道页同一套。

### 3.7 配置兜底（零配置可用）

三份配置的**完整默认字典内置在 `app/design/heat/data.py`**，作为只读兜底：

| 配置 | 内置内容 | 用户覆盖文件 |
| --- | --- | --- |
| 污垢热阻 | §5.6 的 9 个键值 | `data/config/hx-fouling.json` |
| 经验 U 区间 | §5.7 的 7 个键值 | `data/config/hx-u-reference.json` |
| 公用工程预设 | §2.2 的 8 档 | `data/config/hx-utility-presets.json` |
| Kern 系数（Phase 2） | 占位系数 + `calibrated=false` | `data/config/hx-kern.json` |

优先级与行为：

1. **文件不存在 → 用内置**，不报错、不提示缺配置；冷启动与离线单测零配置即可跑。
2. **文件存在且合法 → 覆盖内置**（不是合并：用户想删掉某一档，就应当能删掉）；界面与导出标 `config_source=user`。
3. **文件存在但 JSON 非法 / 字段类型错 → 回退内置 + `warning`**，不让一个坏配置文件把页面打挂。
4. **只有用户在界面点「保存」才写盘**，原子替换（`.tmp` + `os.replace`）；程序自身不主动生成这些文件。
5. 单测只 import 内置字典，不依赖 `data/` 目录存在；涉及覆盖逻辑的用例用临时目录。


---

## 4. 模块划分与接口定义

### 4.1 依赖方向（沿用 `app/README.md` 的单向约束）

```
aspen(read) → values
design/heat → values(仅单位/常量) + hydraulics(仅 λ/Re)
store → design/heat(不反向)
export → design/store
api → 全部
web → api
```

`design/heat/` **不 import `app.aspen`**；缺物性由调用方（api 层）先读好再传入，保持离线可测。

### 4.2 新增文件

| 文件 | 职责 | 关键函数（签名以实现为准，语义冻结） |
| --- | --- | --- |
| `app/aspen/reader.py`（改） | 读公用工程树、块的 `UTILITY_ID`、`HX_UA`；Phase 2 读侧物性 | `read_utilities(session) -> dict`；`read_heat_utilities(session, blocks) -> None`；`read_hx_props(session, streams, blocks, connections) -> None` |
| `app/aspen/units.py`（改） | 新增 `ua`（W/K）与 `viscosity`、`specific_heat`、`conductivity` 量纲 | `convert("ua", …)` 等 |
| `app/design/heat/__init__.py` | 包入口，导出稳定 API | — |
| `app/design/heat/params.py` | 侧数据装配、LMTD、面积溯源、设计温压、允许压降、污垢、`U_back`、边界拦截 | `resolve_sides()`、`lmtd()`、`resolve_area()`、`design_pressure()`、`design_temperature()`、`allowable_dp()`、`fouling_factor()`、`u_back()`、`check_boundary()`、`build_case(block, sides, design, props)` |
| `app/design/heat/model_code.py` | GB/T 151 编码（bar → MPa、缺项留空） | `build_code(parts) -> {"code", "code_partial", "missing"}` |
| `app/design/heat/geometry.py`（Phase 2） | 标准行枚举、管数、管程/壳程水力、总 U、接管 | `enumerate_rows()`、`tube_side()`、`shell_side()`、`overall_u()`、`nozzle_mm()` |
| `app/design/heat/rating.py`（Phase 2） | 五态判定与检查清单 | `assess(case, geometry) -> {"verdict", "checks", "missing", "warnings"}` |
| `app/design/heat/data.py` | 污垢表、经验 U 区间、公用工程预设、标准表加载与降级 | `fouling_table()`、`u_reference()`、`utility_presets()`、`standard_snapshot()`、`setup_guide()` |
| `app/design/heat/prompt.py` | 「复制填写提示词」正文（标准表 + EDR 送审），三处出口同源 | `standards_prompt()`、`edr_prompt()` |
| `app/store/db.py`（改） | 三张表 + 迁移 + 读写 | `get_hx_design/save_hx_design`、`get_hx_selection/save_hx_selection`、`get_hx_props/put_hx_props` |
| `app/api/routes/heat.py` | 列表、详情、设计输入、台账、标准表、导出 | 见 §4.4 |
| `app/api/routes/__init__.py`（改） | 路由表加行（唯一入口） | — |
| `app/export/hx_schedule.py` | 《换热器选型一览表》Word | `build_hx_docx(conn, case_id) -> (bytes, filename)`（仿 `pipe_schedule.build_pipe_docx`） |
| `app/export/hx_edr.py` | EDR 输入参数清单 xlsx | `build_edr_xlsx(conn, case_id) -> (bytes, filename)` |
| `app/export/workbook.py`（改） | 整本 Excel 增「换热器」页 | — |
| `app/export/aipack.py`（改） | AI 包增 `heat_exchangers.csv` + `hx_prompt.md` | — |
| `app/web/static/views/heat.js` | 换热页抽屉：条件、来源徽标、几何候选、台账 | — |
| `app/web/static/views/table.js`（改） | 换热家族行加「选型」入口 | — |
| `docs/casebook-hx-calc-manual.md/.docx` | 换热器计算说明书 | 与 `hx` 手册路由同源 |

### 4.3 核心数据结构（前后端契约，Phase 1 冻结）

`GET /api/cases/{id}/heat` 返回：

```json
{
  "case_id": 72,
  "items": [
    {
      "block": "E0102",
      "kind": "heatx",
      "type": "HeatX",
      "duty_kw": 467.5,
      "sides": {
        "hot":  {"stream": "…", "t_in_c": 210.0, "t_out_c": 205.0, "p_in_bar": 50.0, "p_out_bar": 49.0, "m_kgh": 37672.0, "vap_in": 1.0, "vap_out": 1.0},
        "cold": {"stream": "…", "t_in_c": 170.5, "t_out_c": 190.0, "p_in_bar": 50.0, "p_out_bar": 49.0, "m_kgh": 8120.0, "vap_in": 1.0, "vap_out": 1.0},
        "utility": null
      },
      "area": {"value_m2": 20.68, "source": "aspen", "note": "U_back=850.0，疑似简捷设计假设"},
      "lmtd_k": 26.6,
      "lmtd_basis": "counter_current",
      "u_back_wm2k": 850.0,
      "u_from_ua_wm2k": 849.8,
      "ua_delta_pct": 0.02,
      "verdict": "reference",
      "blockers": [],
      "warnings": ["面积来自 Aspen 简捷设计（U=850），非 rating 结果"],
      "missing": [],
      "provenance": {"area": "aspen", "utility": "none", "props": "missing"},
      "design": {"p_tube_bar": 55.0, "p_shell_bar": 63.25, "t_tube_c": 235, "t_shell_c": 215, "dp_tube_bar": 10.0, "dp_shell_bar": 11.5, "tube_fluid": "cold", "tube_fluid_source": "rule"},
      "selection": null
    }
  ],
  "std_state": {"state": "missing", "reason": "未加载 GB/T 28712.2 标准系列表"}
}
```

- 所有数值字段缺失一律 `null`，不出现 `0` 顶替、不出现空字符串混用。
- `provenance` 为枚举字符串（见 §7），前端据此画徽标，导出据此加「数据来源」列。

### 4.4 HTTP 接口（加接口 = 路由表加一行 + 域内加方法）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/cases/{id}/heat` | 换热设备列表（含实时派生、溯源、拦截原因） |
| GET | `/api/cases/{id}/heat/{block}` | 单台详情（条件、几何候选、缺失项） |
| POST | `/api/cases/{id}/heat/{block}/design` | 保存设计输入/覆盖（带 `version`，409 防覆盖） |
| POST | `/api/cases/{id}/heat/{block}/selection` | 保存选型台账 |
| GET | `/api/hx-spec` | 口径常量：默认裕量、温压偏移、污垢表、经验 U、状态枚举 |
| GET | `/api/hx-standards` | 标准表状态（missing / invalid / partial / ok + 明细） |
| POST | `/api/hx-standards` | 导入两份 CSV，暂存校验后原子切换版本 |
| GET | `/api/hx-standards-prompt` | 「复制填写提示词」正文 |
| GET | `/api/hx-standards-template.zip` | 空模板（表头 + 2 行不可直接导入的示例） |
| GET | `/api/cases/{id}/heat.docx` | 《换热器选型一览表》Word |
| GET | `/api/cases/{id}/heat-edr.xlsx` | EDR 输入参数清单 Excel |
| GET | `/api/manuals/hx.docx` | 换热器计算说明书 |

（`/api/manuals/(pipe|pump)` 的正则扩为 `(pipe|pump|hx)`，仍在同一行路由。）

### 4.5 与既有模块的复用（不新起炉灶）

- **摩擦系数与 Re**：直接调 `app/design/hydraulics.solve()` / `friction_factor()`（Colebrook 或顾毓珍），管程压降不再写第二套 λ 口径。
- **接管圆整**：复用管道页已导入的 GB/T 17395 表（`app/design/pipe/data.py` 的 `StandardsSnapshot`），不新增一套钢管数据；表未录时只给理论内径。
- **Word 版式**：复用 `app/export/docx_kit.py` 的封面、页眉、三线表、`_body_section`。
- **台账与乐观锁**：照 `pipe_run` 的 `version` 字段与 409 处理。
- **模型身份**：`model_identity.key_of()`，与泵台账同键。

---

## 5. 计算口径与单位制

### 5.1 单位制约定

| 层 | 规则 |
| --- | --- |
| 内部计算 | 一律 SI：W、m²、W/(m²·K)、K、kg/s、Pa·s、J/(kg·K)、W/(m·K)、m/s、Pa |
| 存储 | 压力 bar、温度 ℃、面积 m²、U 为 W/(m²·K)、长度 mm、裕量小数、污垢 m²·K/W |
| 展示 | 与项目一致：℃ / bar / kg·h⁻¹ / m³·h⁻¹ / kW / m²；U 用 W/(m²·K) |
| 型号编码 | 压力段按 **MPa** 写入（1 bar = 0.1 MPa），这是三份文件的写法，也是最容易错的一处 |
| 公式书写 | `ΔT` 在传热里是温差，数值上 K 与 ℃ 等价，不做 +273.15 变换 |

### 5.2 LMTD 与 F

逆流（第一版默认）：

```
ΔT1 = T_hot_in  − T_cold_out
ΔT2 = T_hot_out − T_cold_in
ΔTm = (ΔT1 − ΔT2) / ln(ΔT1 / ΔT2)
```

- `|ΔT1 − ΔT2| ≤ 0.5 K` 时用算术平均，避免除零。
- 一侧为公用工程冷凝（等温）时，该侧进出口温度相同，公式照用，并标 `utility_isothermal`。
- **F 修正**：第一版只对「1 壳程 / 1 管程」取 `F = 1`（纯逆流）。管程数 > 1 时：
  - 用户填了 `f_correction` → 用；
  - 没填 → `F = null`，`A_req` 标注「未含 F 修正」，判定降为 `reference`；
  - 不内置查图拟合（避免造一个没有出处的 F 曲线）。
- Aspen 侧读数作为交叉校核与兜底：
  - `HX_DTLM`（℃）与自算 LMTD 比较，差 > 2% 给 `LMTD_MISMATCH` 警告；
  - `HX_FMTD` 有非 0 值时**直接作为 F**（多管程不再要求用户查图）；`HX_FMTD = 0` 视为未提供；
  - `HX_DTLM` 是否已含 F 尚无多管程样本可验证（本机 4 台均 1 壳程、F=1，无法区分），展示时写「Aspen 平均温差」并同时给自算值。

### 5.3 反算 U 与 UA 交叉校核

```
U_back = |Q| / (A · ΔTm)          [W/(m²·K)]
U_UA   = UA / A                    （UA 来自 HX_UA，cal/sec-K → W/K）
```

- 两者都有值时给 `|U_back − U_UA| / U_UA`；> 2% 视为「面积或温度读数不自洽」，标 `warning` 并要求复核。
- 面积来源为简捷设计时，`U_back` 只是「模型里假设的 U」，不能当设计值（见 §7 的 `area_basis_hint`）。
- `HX_UAVD`（`cal/sec-cm²-K`，本项目换算为 W/(m²·K)）给出模型自带的设计 U：
  - 与 `U_back` 差 ≤1% → 面积与假设 U 自洽；
  - `HX_EX_AREA / HX_EX_U / HX_EX_Q / HX_ZO_*` **全为 0** → `mode = shortcut`，`area_basis_hint = shortcut_design`；
  - 任一项非 0 → `mode = rating`，`area_basis_hint = rating`。这比「U 恰好 850」的猜测更可靠。

### 5.4 设计压力与设计温度

设计压力（逐侧）：

```
P_op > 1.0 bar  →  P_d = 1.1 × P_op，向上圆整到 0.01 bar
P_op ≤ 1.0 bar  →  P_d = −1.0 bar（外压工况），并标「按真空/外压取」
```

- 允许逐侧覆盖（`hx_design.p_design_*`）；覆盖值在界面与导出中标「人工」。
- 三份文件里「5 MPa 写成 5.5 bar」的单位错不复制；工具只认 `bar`，显示时同时给 MPa 供编码与对照。

设计温度（逐侧）：

```
T_d = ceil_to_5( max(T_in, T_out) + offset )，offset 默认 20 ℃（允许 15~30）
```

- 案例实际取 10~19 ℃，与正文「15~30」并不一致；因此 offset 做成可配置，并在界面注明「规范建议 15~30，案例实际偏小，属设计判断」。
- 负温侧（如 7→12 ℃ 冷冻盐水）按同式向上圆整，不设「最低 30 ℃」这类硬规则。

### 5.5 允许压降

```
出口绝压 < 0.1 MPa（1 bar）  → 允许 Δp = 0.40 × 进口绝压
出口绝压 ≥ 0.1 MPa            → 允许 Δp = 0.20 × 进口绝压
```

- 逐侧算，允许覆盖；覆盖值标「人工」。
- 这是 EDR 的设置惯例（三份文件一致），不是国标强制值；文案写「EDR 惯例」。

### 5.6 污垢热阻

- 查表键 → 数值，来源写在 `fouling_*_key`，界面显示「查表：冷却水 0.000172」。
- 默认表（`data/config/hx-fouling.json`，可编辑；缺文件时用内置最小集）：

| 键 | 建议值 m²·K/W | 说明 |
| --- | --- | --- |
| `cooling-water` | 0.000172 | 冷却水 |
| `steam` | 0.000086 | 水蒸气 |
| `brine` | 0.000172 | 盐水 / 冷冻盐水 |
| `organic-liquid` | 0.000172 | 有机液体 |
| `organic-vapor` | 0.000086 | 有机蒸气 / 塔顶蒸汽 |
| `reboiler-fluid` | 0.000516 | 再沸器物料 |
| `refrigerant` | 0.000176 | 制冷剂 |
| `hydrogen-rich-gas` | 0.000180 | 加氢循环气 |
| `clean-gas` | 0.000034 | 清洁气体（空气类） |

- 数值取自三份文件引用的《化工工艺设计手册》与《换热器工艺设计》，**属工程经验值，不是标准强制值**；允许逐台覆盖，覆盖后不再显示查表键。

### 5.7 面积与裕量

```
A_req = |Q| / (U_assumed · ΔTm · F) × (1 + margin)
```

- `U_assumed` 优先取人工值；否则按经验区间取中值（`data/config/hx-u-reference.json`）：

| 键 | 区间 W/(m²·K) | 默认 | 适用 |
| --- | --- | --- | --- |
| `water-water` | 800–1500 | 1000 | 水–水 |
| `organic-water` | 500–1200 | 700 | 有机液–水 |
| `gas-liquid` | 200–600 | 350 | 气体–液体 |
| `gas-gas` | 50–250 | 120 | 气体–气体 |
| `condensing-steam` | 1000–2000 | 1500 | 蒸汽冷凝–水 |
| `condensing-organic` | 600–1200 | 800 | 有机蒸气冷凝 |
| `boiling-organic` | 500–1500 | 800 | 有机液沸腾 |

- 区间取自手册经验范围，并用本机 4 个案例的 `U_back`（344.9 / 1115.8 / 1292.5 / 1892.6）做了范围校核；仍是经验值，标「参考」。
- 冷凝/沸腾两档只用于「公用工程侧冷凝」或用户在外部估算时手选；**工艺侧发生相变时仍按 §6 拦截**，不得用这些经验值绕过拦截。
- `margin` 默认 0.40（案例 34%~44% 的中位附近），允许 0.30~0.50；超出区间给 warning，不拒算。
- `A_req` 与 `area_nominal_m2` 分开存：前者是需求，后者是标准公称值（编码只用后者）。

### 5.8 几何（Phase 2）

面积基准统一为**管外表面**：

```
A = n · π · d_o · L        （d_o、L 单位 m）
```

- 管数：`n = ceil(A_req / (π · d_o · L))`；标准表可用时优先枚举表内行，不自算。
- 下文 `D_s` 一律指壳体内径（m）；`P` 为管心距（m），`B` 为折流板间距（m）。
- 管程流通面积：`A_i = (n / passes) · π · d_i² / 4`，`d_i = d_o − 2·wall`
- 管程流速：`u_i = V / (3600 · A_i)`（V 为 m³/h）
- `Re_i = ρ · u_i · d_i / μ`，`Pr = cp · μ / λ`
- 管程膜系数：`Nu = 0.023 · Re^0.8 · Pr^0.4`（冷却时 0.3），`h_i = Nu · λ / d_i`
- 壳程（Kern）：

```
d_e = 4 · ( (√3/2)·P² − (π/4)·d_o² ) / (π · d_o)      （正三角形）
A_s = D_s · (P − d_o) · B / P                         （B = 折流板间距）
G_s = m / A_s
Re_s = d_e · G_s / μ
Nu_s = 0.36 · Re_s^0.55 · Pr^(1/3)
h_o = Nu_s · λ / d_e
```

- 总传热系数（以管外表面为基准）：

```
1/U_o = 1/h_o + R_fo + (d_o/(2·λ_w))·ln(d_o/d_i) + R_fi·(d_o/d_i) + (d_o/d_i)/h_i
```

- 上述 `h` 与 `U` 是**简捷估算**：Kern 壳程偏差可达 2–3 倍，不能称 rating。

### 5.9 压降（Phase 2）

- 管程：

```
Δp_i = ( λ · (L·Np)/d_i + Σζ ) · ρ·u_i² / 2      [Pa]
Σζ 默认 = ζ_in + ζ_out + 4·Np（回弯），系数可配
```

  `λ` 复用 `hydraulics.friction_factor()`（默认 Colebrook，可选顾毓珍/手填），不另写一套。

- 壳程（Kern 形式）：

```
Δp_s = f_s · G_s² · (N_b + 1) · D_s / (2 · ρ · d_e)
```

  `f_s` 由 `Re_s` 按 Kern 摩擦系数曲线取值；拟合系数放 `data/config/hx-kern.json`，**未与 EDR 对标前只作量级参考**，UI 必须注明。
- 验收对标（仅量级）：三份文件的 4 个算例，管程/壳程 Δp 估算与文档值同量级（容差 ±50%）；不达标就降级为「仅供参考」，不硬凑。

### 5.10 接管（Phase 2）

```
d = sqrt( 4 · (V/3600) / (π · u) )      [m] → mm
```

- 流速默认：液体 1.5 m/s、气体 15 m/s、两相 8 m/s（与管道页同一默认口径，可改）。
- 圆整复用 GB/T 17395 表；表未录时只给理论内径并标「未圆整」。
- 管程/壳程各进出口四个接管，按流股实际体积流量分别算，不共用一个值。

### 5.11 GB/T 151 型号编码

格式（GB/T 151-2014）：

```
<前端><壳体><后端><DN>-<Pt>/<Ps>-<A>-<L>/<d>-<N><管束级别>
```

| 段 | 含义 | 本工具取值 |
| --- | --- | --- |
| 前端 | A 平盖 / B 椭圆封头（可拆）/ C 椭圆封头（不可拆） | 默认 B |
| 壳体 | E 单进单出 / F 双进单出 / K 釜式 | 默认 E |
| 后端 | M 固定管板（带管箱）/ S 浮头 / U U 形管 / T 填料函 | 默认 M |
| DN | 公称直径 mm | 标准表行或人工 |
| Pt/Ps | 管程/壳程设计压力 **MPa** | `bar / 10`，保留 3 位小数（去掉多余 0） |
| A | 公称换热面积 m² | **必须是 `area_nominal_m2`**，不是 `A_req` |
| L/d | 公称管长 m / 换热管外径 mm | 标准表行或人工 |
| N | 管程数 | 1/2/4/6 |
| 级别 | I / II | 默认 I |

规则：

1. **缺任一段 → 不输出完整 `code`**，只输出 `code_partial`（缺项用 `?` 占位）与 `missing[]`。
2. `area_nominal_m2` 缺失时不出编码（用 `A_req` 冒充公称面积是错误做法）。
3. 压力段为负（外压）时保留负号，如 `-0.1`。
4. 示例校验：用两组构造的 GB/T 151 编码串逐段还原（其中一组含负压壳程），不引用具体项目型号。

### 5.12 管程 / 壳程流体分配（`tube_fluid`）

**为什么单列一条**：设计压力/温度、污垢热阻、允许压降、流速、压降、接管尺寸全部按「管程」「壳程」分别取；若冷/热/公用工程侧与管壳程的映射不清楚，后面每一项都会错位。

- 枚举：`hot` / `cold` / `process` / `utility`，表示**哪一侧走管程**，另一侧自动走壳程。
  - `HeatX`：只能取 `hot` 或 `cold`；
  - `Heater` / `HeaterX`：只能取 `process` 或 `utility`。
- 存储：`hx_design.tube_fluid`；来源写 `tube_fluid_source`（`user` / `rule`）。
- 缺省时按下列规则给出**建议值**（只建议、可覆盖，且标 `rule`）：

| 优先级 | 判据 | 建议走管程 | 理由 |
| --- | --- | --- | --- |
| 1 | 介质有腐蚀性 / 只有管束需要合金 | 腐蚀侧 | 只换管束，壳体用碳钢 |
| 2 | 两侧压差大、高压侧压力高 | 高压侧 | 壳体承压比管束贵得多 |
| 3 | 易结垢、需机械清洗 | 易结垢侧 | 管内可通刷/胶球 |
| 4 | 公用工程为冷却水 | 冷却水 | 常规做法，流速易保证 |
| 5 | 公用工程为蒸汽冷凝 / 制冷剂蒸发 | 工艺侧（公用工程走壳程） | 壳程压降小、便于排液/排气 |
| 6 | 大流量、低粘度 | 壳程侧 | 壳程流通面积大，Δp 低 |
| 7 | 以上都不适用 | `HeatX` 取 `cold`；`Heater` 取 `process` | 与「冷却水走管程、被加热物料走管程」的课堂惯例一致 |

- 规则实现：`data.tube_fluid_suggestion(kind, sides, utility_phase, pressures)` 返回 `{value, reason, matched_rule}`；界面显示「建议：冷侧走管程（规则 4：冷却水）」。
- 一致性校验：
  - `HeatX` 传 `process` / `utility` → 拒绝保存，属枚举越界；
  - `Heater` 传 `hot` / `cold` → 同上；
  - 同一台设备不能出现两个「管程」侧。
- 影响范围：`p_design_*`、`t_design_*`、`dp_allow_*`、`fouling_*`、Phase 2 的 `u_i` 与 `Δp_i` 一律按该映射取值；L1 的 LMTD 与分配无关，缺分配时仍可算 LMTD，但设计温压/污垢/允许压降按规则建议并标 `rule`。


---

## 6. 工况边界拦截

### 6.1 判定状态（五态，比泵的三态多两档）

| 状态 | 含义 | 允许出现的数字 |
| --- | --- | --- |
| `blocked` | 工况本身不适用（相变、温度交叉、近等温、多股、零负荷） | **不输出** LMTD / U / A_req，只输出条件与原因 |
| `unknown` | 关键数据缺失（公用工程温度、面积、物性、标准表） | 输出已算出的部分，缺项列 `missing[]` |
| `reference` | 含假设（预设公用工程、人工 U、人工面积、未含 F） | 可输出，全部标「参考」 |
| `pass` | 全部关键输入为实测/标准，简捷校核通过 | 文案：`简捷校核通过（Kern 口径）` |
| `fail` | 同上但任一项超限 | 文案：`简捷校核未通过（Kern 口径）` |

**硬规则**：只要 `provenance` 里有一项不是「实测」，就不允许 `pass/fail`，只能是 `reference`。

例外说明：`utility_isothermal`（饱和蒸汽/制冷剂等温冷凝或蒸发）**本身不降级**——它是合法的等温边界条件；是否 `pass` 仍取决于公用工程温度来源（`aspen` 可 `pass`，`preset` 只能 `reference`）与其余输入。

### 6.2 拦截规则（`blocked` 命中即停；`unknown` 记录后继续；`warning` 只累加）

| 代码 | 条件 | 处理 |
| --- | --- | --- |
| `PHASE_CHANGE` | **工艺侧**任一侧 `abs(vap_out − vap_in) > 0.005` | `blocked`；提示转 EDR 分区 |
| `TWO_PHASE` | **工艺侧**任一端 `0.02 < vap < 0.98` | `blocked`；两相流不套单相 LMTD |
| `NEAR_ISOTHERMAL_PROCESS` | **工艺侧** `abs(T_in − T_out) < 2 K` 且 `abs(Q) > 0.5 kW` | `blocked`；可能是相变或极大热容 |
| `TEMP_CROSS` | `ΔT1 ≤ 0` 或 `ΔT2 ≤ 0` | `blocked`；逆流已无推动力，需多壳/多台串联重新布置 |
| `ZERO_DUTY` | `abs(Q) ≤ 0.5 kW` | `blocked`（无换热任务） |
| `ZERO_FLOW` | 任一侧 `m ≤ 0` 或 `V ≤ 0` | `blocked`（无法定流速） |
| `MULTI_STREAM_SIDE` | 同一侧进流股数 > 1 | `blocked`（第一版只支持双股） |
| `MISSING_SIDE` | HeatX 缺 hot/cold；Heater 缺公用工程温度 | `unknown`，`missing` 写明 |
| `UTILITY_NO_TEMP` | 公用工程类型为 `ELECTRICITY` / `FUEL` | `blocked`：电加热/燃料没有「另一侧温度」，不套 LMTD |
| `MISSING_UTILITY_T` | 公用工程为 `STEAM` / `WATER`，但 `UTL_IN_TEMP/OUT_TEMP` 为空 | `unknown`；提示选预设或手填 |
| `MISSING_AREA` | 无 Aspen 面积且无人工面积 | `unknown`；只能出条件表与 `A_req`（若 U 已给） |
| `MISSING_PROPS` | Phase 2 缺 cp/λ/μ | `unknown`（L1 不受影响） |
| `NO_STANDARD_TABLE` | 未导入/不完整标准表 | 仅影响 L2：`A_req` + 推荐范围，`std_skipped=true` |
| `SMALL_TERMINAL_DT` | `min(ΔT1, ΔT2) < 5 K` | 不拦截，`warning`（LMTD 对温差极敏感） |
| `LOW_HOT_END_DT` | `ΔT_hot_end < 20 K` | 不拦截，`warning`（文件要求热端 ≥20 ℃） |
| `F_NOT_APPLIED` | 管程数 > 1 且未填 F | 不拦截，`A_req` 标「未含 F」 |
| `AREA_SHORTCUT_HINT` | `U_back` 与常见简捷值（如 850）吻合且面积来源为 Aspen | 不拦截，`warning` + `area_basis_hint` |
| `UA_MISMATCH` | `abs(U_back − U_UA) / U_UA > 2%` | 不拦截，`warning`（面积或温度可能不自洽） |
| `LMTD_MISMATCH` | `abs(LMTD_自算 − HX_DTLM) / HX_DTLM > 2%` | 不拦截，`warning`（温度读数或流向假设有问题） |
| `UTILITY_ISOTHERMAL` | 公用工程 `abs(T_in − T_out) ≤ 0.5 K` 且相态为 `condensing` / `boiling` | **不拦截**；按等温侧算 LMTD，标 `utility_isothermal` |
| `UTILITY_PHASE_T` | 公用工程相态为相变，但 `abs(T_in − T_out) > 0.5 K` | 不拦截，`warning`：可能含过热/过冷段，需核对预设 |

> **相变拦截只针对工艺侧流股。** Heater 的公用工程侧不是流股，读不到 `vap`，不能也不该套用工艺侧的相变规则。饱和蒸汽冷凝、制冷剂蒸发这类等温相变公用工程按 `UTILITY_ISOTHERMAL` 正常进入 LMTD 与 L1 计算，`utility_source` 照实标 `aspen` / `preset`；只有当工艺侧本身发生相变时才 `blocked`。


### 6.3 提示文案（UI 与导出同源）

- `PHASE_CHANGE`：「该设备进出口汽化率变化 x→y，属相变工况；纯显热 LMTD 不适用。请在 Aspen EDR 中按 Zone 分段计算，本工具不给出 U 与面积结论。」
- `UTILITY_ISOTHERMAL`：「公用工程为 x ℃ 等温相变（蒸汽冷凝/制冷剂蒸发），按等温侧计算 LMTD；该侧来源：Aspen/预设。」
- `UTILITY_PHASE_T`：「公用工程标为相变介质，但进出口温差 x K，可能含过热或过冷段；请核对预设或改用手填两端温度。」
- `TEMP_CROSS`：「冷端/热端温差为负（ΔT1=…，ΔT2=…），单台逆流已无法实现该换热任务；请增加壳程/串联台数或调整公用工程温度。」
- `NEAR_ISOTHERMAL_PROCESS`：「工艺侧温差仅 x K 却有 y kW 负荷，可能是相变或大热容介质；LMTD 方法不成立。」
- `NO_STANDARD_TABLE`：「未加载标准系列表，已跳过标准规格圆整；以下仅为理论需求面积与经验推荐范围。」

---

## 7. 数据溯源标签（硬约束）

### 7.1 枚举

| 字段 | 取值 | 含义 |
| --- | --- | --- |
| `area_source` | `aspen` / `manual` / `edr` / `none` | 面积来自块读数 / 人工录入 / EDR 校核结果 / 无 |
| `area_basis_hint` | `shortcut_design` / `rating` / `unknown` | Shortcut 简捷设计 / EDR 校核模式 / 读不到 |
| `u_basis` | `aspen_u` / `back_calc` / `assumed` / `rating` | Aspen 自带设计 U（`HX_UAVD`）/ 反算 / 人工假设 / Phase 2 计算 |
| `utility_source` | `aspen` / `preset` / `manual` / `none` | 公用工程树实测 / 预设库 / 手填 / 无 |
| `prop_source` | `aspen` / `derived` / `manual` / `missing` | 物性实测 / 由 Q、m、ΔT 反推 / 手填 / 缺 |
| `geom_source` | `standard` / `estimate` / `manual` / `none` | 标准表选中 / 经验估算 / 人工 / 无 |
| `p_source` / `t_source` | `rule` / `manual` | 按规则算 / 人工覆盖 |
| `tube_fluid_source` | `rule` / `user` | 管程/壳程分配来自规则建议 / 人工指定 |
| `utility_isothermal` | `true` / `false` | 公用工程侧是否为等温相变（不降级，仅标记） |

### 7.2 渲染规则

1. **界面**：每个关键数字右侧带小徽标（`Aspen` / `手填` / `预设` / `估算` / `EDR`），鼠标悬浮给出来源路径或查表键。
2. **台账**：固定四列「面积来源 / 公用工程来源 / 物性来源 / 几何来源」，不按单元格散落。
3. **Word / Excel**：每张表末尾加一列「数据来源」，格式如 `面积:Aspen简捷｜公用:预设LS135｜U:反算`；表头上方一行提示「本表含 N 项假设，仅供方案比选」。
4. **AI 包**：`heat_exchangers.csv` 每行带 `area_source`、`utility_source`、`prop_source`、`geom_source`、`verdict`，`README.md` 的字段字典里解释枚举。
5. **禁止**：把 `preset` / `assumed` / `derived` 值写成「实测」「校核值」「合格」；禁止在 `pass/fail` 判定里混入假设输入。

### 7.3 面积来源解析顺序

```
1) hx_design.area_source ∈ {manual, edr} 且 area_manual_m2 有值  → 用它
2) block.area_m2 有值                                          → aspen
3) 都没有                                                       → none
```

- `aspen` 时再判 `area_basis_hint`：`U_back` 与 850（或其他整百值）在 ±1% 内，且 `UA/A` 与之同值 → `shortcut_u_suspect`，提示「模型用简捷设计指定了 U」。

---

## 8. 标准系列表（外置 CSV + 缺表降级）

### 8.1 合规前提

GB/T 28712.2 与 GB/T 151 的**数值表**受著作权保护，仓库与安装包都不带；只带表头模板、字段说明与填写提示词。用户从合法渠道取得标准后自行整理成 CSV，导入后立即生效。

### 8.2 文件与字段

存放：`data/preview/hx-std/docx/`（老位置兜底），版本化目录 `data/preview/hx-std/versions/<时间戳>/`，指针文件 `active.txt`；切换方式照 `app/design/pipe/data.py` 的「暂存 → 试加载 → 原子替换」。

**文件 1：`hx-28712-2.csv`（标准系列行）**

- 版本：三份文件引用的是 **GB/T 28712.2-2012**，用户要求按 **2023** 版；CSV 的 `standard` 列写实际版本，两版数值不同不得混用，界面显示当前生效版本号。

| 列 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `DN` | 是 | int | 公称直径 mm |
| `shell_id_mm` | 是 | float | 壳体内径 mm |
| `shell_od_mm` | 否 | float | 壳体外径 mm（缺省 = DN） |
| `tube_od_mm` | 是 | float | 换热管外径 mm（本工具支持 10~57） |
| `tube_wall_mm` | 是 | float | 管壁厚 mm |
| `tube_length_mm` | 是 | int | 公称管长 mm |
| `tube_pitch_mm` | 是 | float | 管心距 mm |
| `tube_passes` | 是 | int | 管程数 1/2/4/6 |
| `tube_count` | 是 | int | 管数 |
| `area_m2` | 是 | float | 公称换热面积 m² |
| `arrangement` | 是 | 枚举 | `triangular` / `rotated_triangular` / `square` / `rotated_square` |
| `pn_bar` | 否 | float | 公称压力 bar（若表里给的是 MPa，请在提示词里换算） |
| `standard` | 否 | text | 如 `GB/T 28712.2-2023` |
| `page` | 否 | text | 页码/表号，便于溯源 |
| `note` | 否 | text | 备注 |

**文件 2：`hx-pass-combinations.csv`（壳程/管程组合）**

| 列 | 必填 | 说明 |
| --- | --- | --- |
| `shell_passes` | 是 | 壳程数（第一版只支持 1） |
| `tube_passes` | 是 | 管程数 |
| `need_f_correction` | 是 | 0/1；1 表示必须给 F |
| `allowed` | 是 | 0/1；0 表示本工具不支持该组合 |
| `note` | 否 | 说明 |

默认内置组合（仅代号与规则，不含标准数值）：1/1（F=1）、1/2（需 F）、1/4（需 F）、2/4（不支持，`allowed=0`）。

- 该文件**可选**：不导入时用上面 4 行内置规则；导入后以文件为准（便于以后加 1/6、2/2 等组合）。
- 组合缺失时按「不支持」处理：`blocked` 文案写「该壳程/管程组合不在允许表内」。

### 8.3 校验（导入时逐行，错误分行返回）

1. 表头齐全、UTF-8、逗号分隔；缺列直接拒。
2. 数值范围：`DN 150~2000`、`tube_od 10~57`、`tube_wall 1~5`、`tube_length 1000~9000`、`pitch ∈ [1.20, 1.70]×tube_od`、`passes ∈ {1,2,4,6}`、`count 1~5000`、`area > 0`。
3. 几何自洽：`|area − n·π·d_o·L / 10^6| / area ≤ 3%`（mm → m² 的换算已含）。
4. 唯一键 `(DN, tube_od_mm, tube_pitch_mm, tube_passes, tube_length_mm)` 不得重复。
5. 完整性：有效行 ≥ 20、DN 档 ≥ 3、管径档 ≥ 2；否则状态为 `partial`，按缺表降级。
6. 同一 `(DN, od, pitch, passes)` 下面积随管长递增；违反给 warning，不拒（可能是分段表）。

### 8.4 空模板与防误导入

- `GET /api/hx-standards-template.zip` 生成两个 CSV：只有表头 + 2 行**故意不自洽**的示例（面积写 `999`），并附 `README.txt` 说明每列怎么抄、单位怎么换。
- 导入时若检测到与模板行完全一致（同样的 2 行指纹），拒绝并提示「这是模板，不是数据」——与管道页模板策略一致。

### 8.5 缺表降级（必须显式）

| 状态 | L1 | L2 |
| --- | --- | --- |
| `ok` | 正常 | 标准行枚举 + 圆整 + 编码 |
| `partial` | 正常 | 只用通过校验的行；不足 20 行视同 `missing` |
| `missing` / `invalid` | 正常 | 只算 `A_req`；按经验给推荐范围（管径 19/25、管长 4.5/6/7.5 m、管数区间）；**不生成型号**，`std_skipped=true`，界面与导出都写「未加载标准系列表，跳过标准规格圆整」 |

推荐范围的口径：

```
管数区间 = [ ceil(A_req/(π·d_o·L_max)), ceil(A_req/(π·d_o·L_min)) ]   （d_o 取 19 与 25 各算一遍）
壳体直径 = 经验估算（Kern 管束直径关联式），标「经验估算，非标准值」
```

- 估算壳径只用于「大概多大」，不写进台账、不进编码、不出现在 Word 型号列。

---

## 9. 页面与导出

### 9.1 页面

- 入口：设备页「换热」家族行尾加「选型」按钮（与泵页一致）。
- 抽屉布局（悬浮窗，仿 `views/pump.js`）：
  - 顶部：设备切换、状态徽标（`参考` / `缺数据` / `已拦截` / `简捷通过`）、来源摘要。
  - 左栏：工艺条件（冷/热/公用工程三块；每块四端温压、流量、相态、组成入口），管程/壳程分配（默认给建议值，可改），以及设计温压、允许压降、污垢（可覆盖）。
  - 右栏：LMTD、`U_back`、`UA` 校核、`A_req`、几何候选表（Phase 2）、压降与判定清单。
  - 底栏：保存（带版本号）、复制 EDR 参数、导出行。
- 未导入标准表时，右栏顶部固定一条黄色横幅，写明「跳过标准规格圆整」。

### 9.2 《换热器选型一览表》Word

列（冻结顺序，供外部粘贴）：

`位号 | 设备名称 | 型号 | 公称面积 m² | 管数 | 管长 m | 管径 mm | 管程数 | 壳径 mm | 壳体材质 | 管束材质 | 台数 | 执行标准 | 数据来源 | 备注`

- 版式复用 `docx_kit` 的三线表；型号缺失时写 `—`，备注列写缺项。
- 表尾自动附「本表含 N 项假设」与「未加载标准表」提示（若有）。

### 9.3 EDR 输入参数清单（xlsx，三页签）

| 页签 | 列 |
| --- | --- |
| `EDR工艺输入` | 位号、侧别、**管程/壳程**、流股、相态、质量流量 kg/h、体积流量 m³/h、进口温度 ℃、出口温度 ℃、进口压力 bar、出口压力 bar、允许压降 bar、设计温度 ℃、设计压力 bar、污垢热阻 m²·K/W、数据来源 |
| `EDR结构输入` | 位号、型式（BEM…）、壳径 mm、管径×壁厚 mm、管长 mm、管数、管程、管心距 mm、排列、折流板型式、间距 mm、圆缺率、壳/管材质、标准行、来源 |
| `结果与提示` | `A_req`、`A_std`、裕量、`U_back`/`U_calc`、Δp 管/壳、判定、拦截/告警、缺失项 |

- 每页首行下方留一行灰字说明单位；不做合并单元格，保证可直接筛选粘贴。
- **落地状态（Phase 2，2026-11-23）**：已按本表拆分为 `EDR工艺输入`（16 列，每台管/壳程两行）/ `EDR结构输入`（16 列）/ `结果与提示`（13 列）+ `说明` 页；第 2 行灰字单位提示、`A3` 冻结、表头筛选；几何列取人工覆盖，缺则回退 L2 推荐标准行并标「L2 推荐」。

### 9.4 计算说明书

- `docs/casebook-hx-calc-manual.md` → 渲染 `.docx`，页面「导出计算书」下发 `/api/manuals/hx.docx`。
- 章节：适用范围与不做的事、符号与单位、LMTD/F、面积与 U、设计温压、允许压降、污垢、几何与压降（Kern 口径）、编码规则、拦截规则、算例（用三份文件的 4 个案例复算 L1，Phase 2 再加几何）、附录 A 代码映射、附录 B 版本记录。

### 9.5 AI 包与整本 Excel

- `heat_exchangers.csv`：每台一行，含条件、`A_req`、`U_back`、判定、四项来源、缺失项。
- `hx_prompt.md`：与「复制 EDR 送审提示词」同源，说明本工具只到简捷级、请对方按 EDR 复核。
- 整本 Excel 增「换热器」页，列与 Word 一览表一致，另加 `verdict` 与 `缺失项`。

---

## 10. 分阶段任务清单

### Phase 1（L1 完整闭环）

| 编号 | 任务 | 产出 | 验收 | 人日 |
| --- | --- | --- | --- | --- |
| P1-1 | 公用工程探针与读取 | `tools/probe_hx.py`；`read_utilities()`、`read_heat_utilities()`；`blocks` 十列 | V15 本机模型：`UTILITY_ID` 17/19、公用工程温度 6/7、`HX_UA` 4/4；读不到留空不猜 | 1.5 |
| P1-2 | 参数与判定模块 | `design/heat/params.py`（含 §5.12 管壳分配建议与校验） | 4 台 HeatX 的 LMTD 与 `HX_DTLM`（52.295 ℃）差 ≤0.1 K；`U_back` 与 `HX_UAVD`（850）差 ≤1%；分配越界被拒 | 2.5 |
| P1-3 | 编码器 | `design/heat/model_code.py` | 两组含负压壳程的编码串逐段还原；缺管程数时不出完整型号 | 1.0 |
| P1-4 | 存储与迁移 | `hx_design`/`hx_selection`/`hx_props`；`_migrate`；读写函数 | 老库可平滑升级；版本冲突返回 409 | 1.0 |
| P1-5 | API | `routes/heat.py` + 路由表 | `test_routes()` 全绿；列表/详情/保存/标准表状态可用 | 1.5 |
| P1-6 | 页面 | `views/heat.js` + 家族入口 | 换热家族每台可开抽屉；徽标与拦截文案正确；保存后可回显 | 2.5 |
| P1-7 | 导出 | Word 一览表、EDR xlsx、Excel 页、AI 包、计算说明书 | 文件可打开；列顺序与本文一致；含「数据来源」列 | 2.5 |
| P1-8 | 自测与文档 | `tests/test_hx.py`；`docs/README.md` 索引；计算书 | 新增 ≥45 条用例全绿；ruff/format/check_render 全绿 | 2.0 |
|  | **合计** |  |  | **14.5** |

#### 落地进度（2026-11-23）

| 编号 | 状态 | 已落地内容 / 下一步 |
| --- | --- | --- |
| P1-1 | ✅ 完成 | `units` 增 `ua`/`u_value`;`read_utilities()` / `enrich_heat_utilities()`;`read_block` 读 `HX_UA/DTLM/FMTD/UAVD` 与 `UTILITY_ID`;`blocks` 加 10 列并迁移;本机 V15 端到端重读 + 入库通过 |
| P1-2 | ✅ 完成 | `params.py`:LMTD(含 flat/cross)、设计温压、允许压降、面积/U 溯源、五态拦截、7 条管壳规则;`data.py` 内置三份默认字典 |
| P1-3 | ✅ 完成 | `model_code.py`:GB/T 151 编码、MPa 换算、缺项占位、枚举校验 |
| P1-4 | ✅ 完成 | `hx_design` / `hx_selection` / `hx_props` 建表;upsert + version 自增;配置覆盖文件原子写 |
| P1-5 | ✅ 完成 | `GET /api/cases/{id}/heat`、设计/台账写入、`GET|POST /api/heat-config`;实测 23 行、越界 400、保存回显通过 |
| P1-6 | ✅ 完成 | 抽屉(只读结果 + 管壳分配/污垢/裕量/F/预设 + 保存/导出);换热家族主表加「状态 / 来源」列(五态徽标 + Aspen简捷/公用工程实测);「默认参数库」三 Tab 可视化编辑,支持保存覆盖与恢复内置(`DELETE /api/heat-config`) |
| P1-7 | ✅ 完成 | Word《换热器选型一览表》15 列(`GET …/heat.docx`)、EDR 清单 xlsx(40 列+说明页)、整本 Excel「换热器」页、AI 包 `heat_exchangers.csv` + `hx_prompt.md`、计算说明书 `docs/casebook-hx-calc-manual.md/.docx` 与 `/api/manuals/hx.docx` |
| P1-8 | ✅ 完成 | `tests/test_heat.py` 10 个用例(50+ 断言)全绿;`tools/check_render.mjs` 纳入 `views/heat.js`;`tools/ui_sweep.py --heat` 实测 HeatX/Heater 抽屉保存、刷新徽标、参数库 user→builtin;`docs/README.md` 已索引说明书与方案 |

> Phase 1 收尾(2026-11-23):批次 1/2/3 全部落地,四道门禁 + 浏览器点击回归通过,进入提交前评审。

> 落库口径：`hx_design` **只存人工决定与覆盖**;面积/U/LMTD/判定每次实时算,不落库。
> 这样换工况或改 Aspen 模型后不会出现"库里的旧结论还挂着"。

### Phase 2（L2 几何初选）

| 编号 | 任务 | 产出 | 验收 | 人日 |
| --- | --- | --- | --- | --- |
| P2-1 | 标准表管道 | 导入/校验/模板/提示词/版本快照/降级 | 模板不能当数据导入；坏表不覆盖当前版本；缺表时明确降级 | 2.0 |
| P2-2 | 几何枚举与管程水力 | `geometry.enumerate_rows()`、`tube_side()` | 导入标准表后能命中 `BEM325-…-35-6/19-1I` 对应行；`u`/`Re` 手算一致 | 3.0 |
| P2-3 | 壳程 Kern、总 U、压降、接管 | `shell_side()`、`overall_u()`、`nozzle_mm()` | 4 个案例 U 在 ±40%、Δp 在 ±50% 内（同量级验收） | 3.5 |
| P2-4 | 五态判定与裕量 | `rating.assess()` | 假设输入不得 `pass`；缺项写清；拦截码与 §6 一致 | 1.5 |
| P2-5 | 页面补几何候选与覆盖 | 候选表、人工覆盖、`std_skipped` 横幅 | 覆盖后重算且不改标准行；恢复自动可回退 | 2.0 |
| P2-6 | 导出补几何与编码 | Word/Excel/EDR 清单三处同步 | 型号、标准行、材质、来源四处一致 | 1.5 |
| P2-7 | 自测与计算书增补 | 用例 ≥35 条；说明书加几何/压降/编码 | 全绿；算例可复现 | 2.0 |
|  | **合计** |  |  | **15.5** |

> 两期合计约 30 人日；Phase 1 完成即可对外使用 L1，Phase 2 不改变任何已发布的表结构与接口。

#### 落地进度（Phase 2，2026-11-23）

| 编号 | 状态 | 已落地内容 / 下一步 |
| --- | --- | --- |
| P2-0 | ✅ 完成 | 探针确认 `STRM_UPP` 默认只有 `PL / MUMX / PBUB`，`CP`/`THK` 需用户挂物性集；`units` 增 viscosity / specific_heat / conductivity；`reader.read_stream_hx_props()`（候选路径 + 子节点扫描 + 单位缺口）与 `read_hx_props()`（只读本台四端）；`api/hx_props.py` 按需读 + `hx_props` 缓存 + `PropsUnavailable` 降级；`cp_from_duty()` 反推（单相、Q≥0.5 kW、ΔT≥2 K）标 `derived` 且不落库 |
| P2-1 | ✅ 完成 | `design/heat/standards.py`（CSV 校验含 A1 面积自洽 3%、状态门槛 20 行 / 3 DN / 2 管径、版本化目录 + 原子指针 + staging 回滚、模板指纹拒绝、`estimate_ranges()` + Kern 管束直径）；`prompt.py`（标准表与 EDR 填写提示词）；`samples/hx-standards-template/` 空模板；本机 φ19/φ25 共 192 行已导入 `version=20261123-205803`；缺表降级 `std_skipped` + `A_req` + 经验范围 |
| P2-2 | ✅ 完成 | `geometry.py`：标准行枚举、管程流通面积 / 流速 / Re / Pr / Dittus-Boelter / h、Darcy 压降（复用 `hydraulics`，ζ=1.5+1.0+1.5×(Np−1)）；`DN325/φ19/6000/1 程` 命中 99 根 34.9 m² |
| P2-3 | ✅ 完成 | `geometry.py`：壳程 Kern（`d_e`、`A_c`、`G`、`Re`、`Nu=0.36Re^0.55Pr^(1/3)`、`f=exp(0.576−0.19lnRe)`、Δp）、`U_clean/U_dirty`、接管按 GB/T 17395 最小可达外径圆整；合成工况 H2O-H2O 与真实 case61 均已跑通（case61 气-气判 fail 且 `U_MISMATCH`，属预期） |
| P2-4 | ✅ 完成 | `rating.py`：五态、压降 / 面积阈值（0.98 / 1.30 / need>1.05）、假设清单 7 类、`select()` 排序与推荐；**含任意假设最高 reference** 由单测锁定 |
| P2-5 | ✅ 完成 | `views/heat.js`：L2 面板（候选表、采用 / 清除、未导入标准表黄条、物性缺失提示、假设说明、复制提示词、下载模板、导入 CSV）；`GET /api/cases/{id}/heat/{block}` 详情 |
| P2-6 | ✅ 完成 | `export/heat.py` 拆为 `EDR工艺输入` / `EDR结构输入` / `结果与提示` + `说明`（16/16/13 列）；`export/hx_schedule.py` 无人工几何时回退 L2 推荐行并在备注标「L2 推荐」，表尾按 L2 判定计数 |
| P2-7 | ✅ 完成 | `tests/test_hx_l2.py` 9 组 90+ 断言（物性换算 / 扫描 / 反推 / 表校验 / 状态与回滚 / 几何水力 / 五态 / 缺表降级 + 三页签 / 详情接口），已登记 `tests/_suite.py`；计算说明书加第 12 章；`selftest` 1544 条、`check_render` 197 条 |

> Phase 2 收尾：四道门禁（`ruff check` / `ruff format --check` / `tools.selftest` / `check_render`）全绿后提交；标准表数值仍不入库，仓库只发布模板与导入管道。

---

## 11. 自测用例规划

新增 `tests/test_hx.py`，接入 `tests/_suite.py` 与 `tools.selftest`；全部离线，不依赖 Aspen。

| 组 | 编号 | 覆盖点 | 断言要点 |
| --- | --- | --- | --- |
| 侧装配 | HX-01~05 | HeatX 热/冷侧、Heater 公用工程侧、缺侧、多股侧、同侧重复连接 | 侧字段与流股一一对应；缺侧 → `missing=["公用工程温度"]` |
| LMTD | HX-06~11 | 逆流、等温差、温度交叉、近等温、零负荷、单侧等温（公用工程冷凝） | 用真实样本：140/80 与 30/85.34 → 52.3 K（±0.1）；交叉 → `blocked` |
| U/UA | HX-12~15 | `U_back`、`UA/A` 交叉、面积缺失、`shortcut` 识别 | `Q=1183.95 kW、A=26.635、ΔTm=52.3` → `U=850±1` |
| 设计条件 | HX-16~20 | 1.1 倍、真空 −1.0 bar、温度 +20 圆整 5、允许压降 20%/40%、人工覆盖优先 | 5.066 bar → 5.57；0.9 bar → −1.0 |
| 污垢/裕量 | HX-21~23 | 查表键、覆盖、裕量区间外 warning | 键与数值成对；覆盖后不再显示查表键 |
| 编码 | HX-24~29 | BEM 完整、负压段、MPa 换算、缺管程、BKU/BEU、缺面积 | `5.57 bar → 0.557 MPa`；缺项只出 `code_partial` |
| 拦截 | HX-30~35 | 相变、两相、温度交叉、多股、零流量、`F` 未填 | 每个码对应唯一状态与文案 |
| 溯源 | HX-36~39 | 四种 `area_source`、公用工程三种来源、导出「数据来源」列 | 假设输入不得 `pass` |
| 标准表 | HX-40~45 | 表头缺失、面积不自洽、管心距超界、重复键、行数不足、模板指纹 | 坏行逐行报错；不足 20 行 → `partial` |
| 导出 | HX-46~49 | Word 列顺序、xlsx 三页签、AI 包字段、空值不补 0 | 列名与本文一致；空值仍为空 |
| 几何/管程（Phase 2） | HX-50~54 | 管数取整、流通面积、流速、Re、λ 复用、标准行命中 | 与手算逐项一致；标准行命中 `DN325/19/6m/99根` |
| 壳程/U/接管（Phase 2） | HX-55~59 | `d_e`、`A_s`、`G_s`、`Nu_s`、`U_o`、Kern Δp、接管圆整 | 4 个案例 U ±40%、Δp ±50%；接管落在 GB/T 17395 档 |
| 迁移 | HX-60~62 | 旧库建表、`blocks` 加列、版本冲突 | 升级后旧工况可读，三列 NULL |
| 管壳分配 | HX-63~65 | 规则建议（冷却水/蒸汽/高压/腐蚀）、枚举越界、缺省标 `rule` | 建议值与理由成对；Heater 传 `hot` 被拒 |
| 配置兜底 | HX-66~68 | 无 JSON、合法 JSON、坏 JSON | 前两者结果正确；坏文件回退内置并给 warning |
| 公用工程相变 | HX-69~71 | 等温蒸汽放行、温差 >0.5 K 告警、工艺侧相变仍拦截 | `utility_isothermal=true` 且不 blocked；工艺侧相变 `PHASE_CHANGE` |
| Aspen 交叉 | HX-72~74 | `HX_DTLM` 与自算一致、`HX_UAVD` 与 `U_back` 一致、`HX_EX_*` 全 0 判 shortcut | 52.295 ℃ ±0.1；850 ±1%；`area_basis_hint=shortcut_design` |
| 公用工程类型 | HX-75~77 | `ELECTRICITY` 拦截、`STEAM` 无温度转预设、`UTL_DUTY` 不参与计算 | 电加热 `UTILITY_NO_TEMP`；`UTL_DUTY=1` 被忽略 |
- 目标：Phase 1 ≥45 条断言、Phase 2 再 ≥35 条断言（每个用例可含多条断言）；保持原 1252 条全绿。
- 现状（2026-11-23）：`tests/test_heat.py` 10 组；`tests/test_hx_l2.py` 9 组 90+ 断言，均已在 `tests/_suite.py` 登记；`selftest` 合计 1544 条。
- 回归要求：`python -m tools.selftest --db data/cases.db`、`node tools/check_render.mjs`、`ruff check`、`ruff format --check` 四道闸门全绿再提交。

---

## 12. 风险与未验证项

| 风险 | 影响 | 处置 |
| --- | --- | --- |
| `UTILITY_ID` / `UTL_IN_TEMP` 已在 V15 实测，但代码尚未实现 | 实现错路径会整块读空 | 按 `tools/probe_hx.py` 的报告落地；读不到留空降级，不猜 |
| `CP` / `THK` 节点路径未实测 | L2 算不出 h | 探针确认；确认不了就只做「U 假设 → A_req」，并在页面写「物性缺，未做传热计算」 |
| 实测 `STRM_UPP` 只有 `PL / MUMX / PBUB`，无 `CP/THK` | L2 的 h 计算缺两项物性 | 需要用户加物性集（同 p_v 的做法），或手填；L1 不受影响 |
| `LS` / `HS` 曾被误判为无温度 | 2 台 Heater 会被误拦 | 实为路径缺 `Output` 一层；复核后 6/7 有温度，仅 `ELE` 真缺 |
| `UTL_DUTY` 读到的是 `1`（标志位） | 若误用会把负荷算成 1 W | 明令禁止使用；负荷只认 `QCALC/HX_DUTY`，并加单测 |
| `HX_DTLM` 是否已含 F 未验证 | 多管程可能重复乘 F | 只在 F=1 的样本上确认过；多管程时同时展示自算与 Aspen 值并给 `LMTD_MISMATCH` |
| `HX_UA` 的 `cal/sec-K` 换算未在本机复测 | UA 互校可能整列偏差 | 新增 `ua` 量纲并加单测；读不到或单位不认识时只跳过互校，不影响 L1 其余结论 |
| HeatX 面积可能是 Shortcut 设计值 | 反算 U 被误读为设计值 | `area_basis_hint` + 强制「参考」徽标；`UA` 交叉校核 |
| Kern 壳程压降/传热偏差 2–3 倍 | 校核结论不可信 | 只称「简捷估算」，验收按「同量级 ±50%」；不做 `rating` 字样 |
| 标准表由用户整理，可能与原文有偏差 | 选型结果错 | 导入时做几何自洽与完整性校验；界面显示表版本与行数；不替代人工核对 |
| 两相/多壳/多股工况 | 结果不适用 | §6 拦截，明确引导 EDR |
| 参考文件自身存在壳径矛盾、型号缺项与压力单位混用 | 被当成模板照抄 | 只提取规则，不复制数值；编码严格按 GB/T 151；算例仅做验收锚点 |
| 多管程 F 需查图 | `A_req` 偏小 | 未填 F 时标「未含 F 修正」，判定降为 `reference` |
| 老工况无公用工程温度 | Heater 无法算 LMTD | 显示缺项，要求重读或手填；不静默补 |
| 导出列顺序被改动 | 影响外部粘贴与模板 | 列顺序在本文冻结；改动需同步计算书与测试 |

---

## 附录 A　字段路径速查

```
\Data\Blocks\<B>\Output\HX_DUTY              HeatX 热负荷（cal/sec 或 MW）
\Data\Blocks\<B>\Output\HX_AREAC             常为空
\Data\Blocks\<B>\Output\HX_AREAC2            换热面积（sqm）
\Data\Blocks\<B>\Output\HX_AREAP             换热面积副本（sqm）
\Data\Blocks\<B>\Output\HX_UA                UA（cal/sec-K）
\Data\Blocks\<B>\Output\HX_DTLM              Aspen 平均温差（C）
\Data\Blocks\<B>\Output\HX_FMTD              F 修正（无量纲，0=未提供）
\Data\Blocks\<B>\Output\HX_UAVD              设计 U（cal/sec-cm²-K）
\Data\Blocks\<B>\Output\HX_NSHLS             串联壳数
\Data\Blocks\<B>\Output\HX_EX_AREA / HX_EX_U / HX_EX_Q / HX_ZO_*   全 0 = Shortcut
\Data\Blocks\<B>\Output\QCALC                Heater 热负荷
\Data\Blocks\<B>\Input\UTILITY_ID            公用工程名
\Data\Blocks\<B>\Input\FCOLD / FHOT           HeatX 冷/热侧入口流股
\Data\Blocks\<B>\Output\COLDIN / COLDOUT      冷侧流股
\Data\Blocks\<B>\Output\HOTIN / HOTOUT        热侧流股
\Data\Utilities\<U>\Input\UTILITY_TYPE        STEAM / WATER / ELECTRICITY / GENERAL
\Data\Utilities\<U>\Output\UTL_IN_TEMP       公用工程进口温度（C）
\Data\Utilities\<U>\Output\UTL_OUT_TEMP      公用工程出口温度（C）
\Data\Utilities\<U>\Output\UTL_TRATE         用量（kg/hr）
\Data\Utilities\<U>\Output\UTL_DUTY          ⚠️ 标志位（1），不是负荷
\Data\Streams\<S>\Output\RES_TEMP / RES_PRES / RES_MASSFLOW / RES_VOLFLOW / RES_VFRAC
\Data\Streams\<S>\Output\STRM_UPP\MUMX       混合物粘度（cP，泵侧已实测）
```

## 附录 B　状态码与错误码

| 类 | 码 | 含义 |
| --- | --- | --- |
| 拦截 | `PHASE_CHANGE` / `TWO_PHASE` / `NEAR_ISOTHERMAL_PROCESS` / `TEMP_CROSS` / `ZERO_DUTY` / `ZERO_FLOW` / `MULTI_STREAM_SIDE` | 见 §6.2 |
| 缺失 | `MISSING_SIDE` / `MISSING_AREA` / `MISSING_PROPS` / `NO_STANDARD_TABLE` | 见 §6.2 |
| 告警 | `SMALL_TERMINAL_DT` / `LOW_HOT_END_DT` / `F_NOT_APPLIED` / `AREA_SHORTCUT_HINT` / `UA_MISMATCH` / `UTILITY_PHASE_T` | 不改变状态，只提示 |
| 标记 | `UTILITY_ISOTHERMAL` | 公用工程等温相变，不拦截、不降级 |
| 标准表 | `HX_STD_HEADER` / `HX_STD_RANGE` / `HX_STD_GEOMETRY` / `HX_STD_DUP` / `HX_STD_INCOMPLETE` / `HX_STD_TEMPLATE` | 导入校验错误 |

## 附录 C　文件与命名清单

```
app/design/heat/__init__.py
app/design/heat/params.py
app/design/heat/model_code.py
app/design/heat/geometry.py        （Phase 2）
app/design/heat/rating.py          （Phase 2）
app/design/heat/data.py
app/design/heat/prompt.py
app/api/routes/heat.py
app/export/hx_schedule.py
app/export/hx_edr.py
app/web/static/views/heat.js
tests/test_hx.py
tools/probe_hx.py
data/config/hx-fouling.json         （不入库）
data/config/hx-u-reference.json     （不入库）
data/config/hx-utility-presets.json （不入库）
data/config/hx-kern.json            （不入库，Phase 2）
data/preview/hx-std/docx/*.csv      （不入库）
docs/casebook-hx-calc-manual.md/.docx
samples/hx-standards-template/*.csv （入库，仅表头 + 2 行不可用示例）
```

## 附录 D　变更记录

| 日期 | 变更 | 说明 |
| --- | --- | --- |
| 2026-11-23 | 初稿 | 按评审意见确定 L1+L2 两期、外置标准表、溯源标签、边界拦截、EDR 清单交付物 |

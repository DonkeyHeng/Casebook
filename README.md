# Casebook

<img src="assets/app-icon_256.png" width="96" align="right" alt="Casebook 图标">

**将 Aspen Plus 仿真结果转化为可检索、可对比、可导出的本地工程数据工具。**

打开已运行的 Aspen 模型，点击「读取当前工况」，即可在本地网页中一站式查看全流程流股、设备参数、塔板剖面与工艺连接图，支持**多工况对比**、**自动管路选型**、**一键生成物能衡算报告**，以及导出结构化数据。

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![安装说明](https://img.shields.io/badge/安装说明-docs%2Finstall.md-4D6BFE)](docs/install.md)

> **免责与版权声明**
> 1. **关于示例数据**：仓库自带的示例数据源自作者个人的 Aspen 课程与竞赛设计，仅用于演示软件功能，可能存在工程误差，**严禁作为实际工程设计依据**。导入你自己的模型后，一切结论请以你的模型为准。
> 2. **关于软件许可**：本项目为个人开源工具，与 AspenTech 无任何关联，亦不包含任何 Aspen 软件本体。使用前请自备正版 Aspen Plus 许可证。

---

## 核心痛点与解决方案

Aspen Plus 擅长流程模拟，但在**数据查阅与报告整理**上并不友好：核对流股组成、对比不同工况的热负荷、整理塔板剖面，往往需要逐格点击复制；当流程出现报错时，异常信息散落在收敛块、模块消息和撕裂流中，很难一眼判断「当前结果是否可用」。

Casebook 只做一件事：**将已有的仿真结果，提取为干净、统一、可离线检索的结构化数据。**

- **无侵入只读附着**：直接连接已打开的 Aspen 实例，仅读取节点数值，不启动新会话、不占用额外许可证，也不会修改或重算原模型。
- **本地离线快照**：一键将全流程数据存入本地 SQLite 数据库，后续无需启动 Aspen 即可随时查阅与导出。
- **智能状态诊断**：自动扫描硬错误与收敛状态；不收敛的模型**照常读取并高亮标黄**，防止误用异常数据。
- **多形态交付**：一键生成可直接贴入设计报告的 Word 衡算书、多工作表 Excel 台账，或供程序/AI 二次分析的 JSON/CSV 数据包。

---

## 快速开始

### 1. 获取与启动

| 使用方式 | 操作步骤 |
| :--- | :--- |
| **免安装版（推荐）** | 前往 [Releases](https://github.com/DonkeyHeng/Casebook/releases) 下载 `Casebook-windows-x64.zip`（约 30 MB），解压后双击 `Casebook.exe` 即可（根目录附带 `安装说明.txt`）。 |
| **源码运行（需 Python 3.10+）** | 克隆或下载源码后，双击根目录的 **`start.cmd`** 启动（带控制台窗口，便于查看运行日志）。 |
| **遇到安装或运行报错** | 请查阅详细文档 **[docs/install.md](docs/install.md)**（纯文本版见 [`docs/install-text.txt`](docs/install-text.txt)）。 |

**命令行启动方式：**
```bash
git clone https://github.com/DonkeyHeng/Casebook.git
cd Casebook
python -m pip install -r requirements.txt
python -m app.api   # 启动后浏览器访问 http://127.0.0.1:8000
```

### 2. 三步上手流程

1. **运行模拟**：在 Aspen Plus 中打开 `.apw` 模型并完成一次完整计算。
2. **读取工况**：在 Casebook 页面左上角点击蓝色按钮 **「读取当前工况」**，等待进度条完成。
3. **查阅与导出**：在左侧栏切换查看流股、设备、流程图或管道选型；点击右上角导出 Excel、Word 衡算报告或数据包。
   > *提示：若需使用管路选型功能，可在「管道」页顶部调整默认流速与管材（默认 S30408）；如需为特定流股指定不同管材，点击右上角「等级表」配置即可。*

### 3. 离线体验（无需 Aspen）

首次打开软件时，本地数据库为空（`data/cases.db` 不随仓库分发），首页会提示「还没有工况」。仓库内附带了一组包含 121 条流股、77 个模块与 7 台泵的真实异常工况快照（含 `T0202` 塔干涸、`T0205` 组分不收敛），可用于完整体验界面与诊断功能：

- **使用 `Casebook.exe`**：直接在首页点击 **「载入示例工况」** 按钮（或运行 `Casebook.exe --sample`），原库会自动备份为 `cases.db.bak`。
- **使用源码**：
  ```bash
  cp data/cases.db data/cases.local.db   # 若已有本地数据请先备份
  cp samples/case-23-error.db data/cases.db
  python -m app.api
  ```
  *示例数据详情见 [`samples/README.md`](samples/README.md)。*

---

## 核心功能一览

| 功能模块 | 说明与特性 |
| :--- | :--- |
| **工况读取** | 任意页面均可触发。自动附着实例 → 提取全流程数据 → 存入本地库 → 切换至新工况。百条流股约需 15–40 秒，支持进度条显示与卡顿提示。 |
| **问题横幅** | 顶部置顶显示运行结论（如*「本次运行有 2 个模块报错，不建议用于计算收率」*），下方附带可折叠的详细报错列表。 |
| **流股与组成** | 统一换算为 `℃ / bar / kg·h⁻¹ / kW`。点击流股名称可查看液相 $x$ / 气相 $y$ 摩尔分数；默认按需读取组成并缓存，侧栏勾选「读全部流股组成」可一次性读取各组分质量流量。 |
| **设备台账与校验** | 按设备族分页签展示（塔 / 换热 / 分离 / 反应 / 泵 / 流体输送 / 混合分流 / 其他），默认按「错误 → 告警 → 正常」排序并附带 Aspen 原始提示。自动执行全设备焓平衡自检（含塔与反应器），右上角支持按当前设备分类导出 Excel。 |
| **泵参数与汽蚀** | 自动提取流量、扬程、效率、轴功率、建议电机及进出口状态。饱和蒸气压 $p_v$ 默认取进口泡点压力并据此计算有效汽蚀余量 $\text{NPSH}_a$（缺失时支持手动补充），支持一键跳转中联泵业选型页。 |
| **交互流程图** | 自动生成分层拓扑连接图，支持缩放平移、点击设备聚焦上下游两层关系，可导出高清 SVG / PNG 图片用于设计报告。 |
| **多工况对比** | 任选两个历史工况，按流股或设备维度对比 `A / B / 差值`，自动高亮状态变更与焓平衡差异。 |
| **塔板剖面** | 展示逐板温度、压力、气液相流量与热负荷分布，提供可视化趋势曲线与联动数据表。 |
| **管路选型** | 实时执行「相态判断 → 流速选取 → 内径计算 → 圆整 DN → 设计温压与许用应力 → 壁厚计算（含负偏差与工艺最小壁厚下限）→ 管道等级号生成」，附带 V1–V9 工程规范校验（如泵入口流速、真空、超温等）。 |
| **物能衡算报告** | 按册或合订导出 `.docx` 格式的《物料衡算报告》与《能量衡算报告》。提供**物料平衡宽表**（流股为列、属性与组分为行）、年消耗/产出量（t/a）、全流程能量汇总及塔负荷明细；支持人工确认工段（界区）边界并附带 $\Sigma\text{进} = \Sigma\text{出}$ 守恒校验。亦支持命令行批量生成：`python -m tools.balance_report --case N`。 |
| **多格式导出** | 支持导出包含 8 张工作表的完整 Excel 台账（概要 / 流股 / 设备 / 泵 / 连接 / 塔剖面 / 管道 / 管道计算书），或导出包含 Markdown 说明 + CSV + JSON 的结构化数据包（`.zip`）。 |

> **质量保障**：项目内置 **433 条**独立回归测试用例（含 `--db` 库一致性校验共 461 条），运行 `python -m tools.selftest` 即可在无 Aspen 环境下完成全链路逻辑验证。

---

## 版本演进

**当前最新版本：[v1.2.0](https://github.com/DonkeyHeng/Casebook/releases/tag/v1.2.0)**（完整更新日志见 [Releases](https://github.com/DonkeyHeng/Casebook/releases)）

- **第一期（核心读取与选型）**：完成工况快照存储、流股/设备/塔剖面/流程图展示、多工况对比、管路自动选型及基础导出。
- **第二期（工程深度与体验）**：新增设备分族视图、泵参数与 $\text{NPSH}_a$ 计算、按分类导出 Excel 及深色主题（全程保持纯只读架构）。
- **v1.2.0（物能衡算报告）**：新增 Word 版《物料衡算报告》与《能量衡算报告》导出，支持工段界区划分与全流程物料/能量守恒校验，并将塔与反应器纳入焓平衡校验范围。

<details>
<summary><strong>点击查看早期版本号对照表（2026-11 版本号规范化重排）</strong></summary>

| 旧版本号 | 现行版本号 | 备注 |
| :--- | :--- | :--- |
| `v1.0.0` | `v1.0.0` | 保留（旧项目名初始版本） |
| `v1.0.1` | `v1.0` | — |
| `v1.0.2` + `v1.0.3` | `v1.0.1` | — |
| `v1.1` | `v1.1` | 保持不变 |
| `v1.2` | `v1.1.1` | — |
| `v1.3` | `v1.1.2` | — |
| `v1.4` | `v1.1.3` | — |

</details>

---

## 环境要求与数据存储

### 运行环境
- **操作系统**：Windows 10 / 11（依赖 Windows COM 接口，不支持 macOS / Linux）。
- **模拟软件**：Aspen Plus **V15**（实测版本，其他版本暂未充分验证），需自备正版许可。
- **运行会话**：必须在**真实桌面会话**中运行（远程桌面服务会话可能无法建立 COM 连接），且本工具需与 Aspen 保持**相同的管理员权限级别**。
- **浏览器**：Edge / Chrome 等现代浏览器（前端零构建，无需 Node.js 环境）。

### 本地数据规范
- **数据隔离**：工况库（`data/cases.db`）、国标管材数据（`data/preview/std/docx/*.csv`）与管道等级配置（`data/config/piping-classes.json`）均保存在本地 `data/` 目录下，**不上传云端、不纳入 Git 版本控制**。
- **单位与容错**：系统统一采用工程常用单位，同时在导出文件中保留 Aspen 原始数值与原始单位；未能匹配换算规则的字段将留空并在顶部「单位诊断」中汇总提示，绝不静默丢弃。

---

## 常见问题 (FAQ)

<details>
<summary><strong>1. 点击「读取当前工况」提示无法连接到 Aspen？</strong></summary>

请依次排查以下三点：
1. 确认 Aspen Plus 已打开并加载了目标模型。
2. 确认软件运行在本地真实桌面会话中。
3. 确认 Casebook 与 Aspen Plus 的权限一致（例如不要一个以管理员身份运行、另一个以普通用户运行）。
若仍无法连接，可运行诊断脚本 `python tools/probe_aspen.py` 排查 COM 接口状态。
</details>

<details>
<summary><strong>2. 为什么流股组成需要点击后才加载？</strong></summary>

完整读取一条流股的组成（气液相摩尔分数 + 各组分质量流量）需要发起 54 次 COM 调用，121 条流股全量读取会使耗时从数秒增加到数十秒。因此软件默认采用「按需读取 + 本地缓存」策略。
- **注意**：若你需要导出包含「各组分质量流量」的《物料衡算报告》，请在读取前勾选侧栏读取按钮下方的 **「读全部流股组成」**（否则报告中仅输出摩尔流量）。
</details>

<details>
<summary><strong>3. 为什么管路选型提示「标准表未录入」？如何补齐？</strong></summary>

管路选型依赖 **GB/T 17395-2024**（表 1、表 4）与 **GB/T 20801.1-2025**（表 B.1）中的管径壁厚与材料许用应力数据。**由于国标原文受版权保护，仓库与安装包内不包含这些数据表。**

**三步快速配置：**
1. 自行获取上述国标文本，整理为三个 UTF-8 编码的 CSV 文件：`t1-17395-table1.csv`、`t4-17395-table4.csv`（需含 `D_mm`、`S_mm` 列）以及 `t3-20801-tableB1.csv`（需含 `标准`、`牌号` 及 `许用应力_<温度>` 列）。
2. 在「管道」页面右上角点击 **「标准表」** 按钮直接导入，程序会自动校验表头与行数，校验通过后**立即生效（无需重启）**。
3. 详细的页码索引见 [`docs/pipe-standards-index.md`](docs/pipe-standards-index.md)；你也可以直接使用 [`docs/pipe-standards-extract-prompt.md`](docs/pipe-standards-extract-prompt.md) 中的提示词，让具备识图能力的 AI 帮你自动提取国标表格。
</details>

<details>
<summary><strong>4. 为什么泵页面的参数或 NPSHₐ 显示为 `—`？</strong></summary>

- **整页为空**：可能是第二期功能上线前读取的旧快照，重新读取工况即可（或运行 `python -m tools.repair_cases --apply` 基于历史原始值重算）。
- **仅 $\text{NPSH}_a$ 为空**：计算需要进口流股的饱和蒸气压 $p_v$（默认读取进口泡点压力 `PBUB`）。可在表格中手动填入，或在 Aspen 中通过五步设置输出该物性（物性组新建 `pv` → 添加 `PL`、`PBUB` → 在报告选项中将该物性组加入流股输出 → 重新运行模型并重读工况，详见 [`docs/pump-selection-plan.md`](docs/pump-selection-plan.md) §3.6）。
- **进口为两相流**：此时汽蚀余量计算公式前提不成立，系统会自动跳过计算并在悬浮提示中说明。
</details>

<details>
<summary><strong>5. 为什么管路壁厚比承压公式算出来的值更厚？</strong></summary>

程序在按 GB/T 20801.1 计算内压壁厚并叠加腐蚀裕量后，还会套用**工艺最小壁厚下限**（DN10–25 取 `2.0 mm`，DN32–100 取 `3.0 mm`）。例如 1.0 MPa 下的 DN15 管道纯承压壁厚仅约 0.07 mm，这属于仪表管/换热管规格，常规工艺管道无法焊接成型。在页面勾选「显示计算过程」可查看具体的下限判定列。
</details>

<details>
<summary><strong>6. 其他常见问题（材质判断、参数修改、不收敛处理）</strong></summary>

- **会自动选材质吗？** 不会。介质腐蚀性受浓度、微量杂质（如氯离子 ppm）和温度影响极大，软件仅依据你配置的 `data/config/piping-classes.json` 匹配材质与腐蚀裕量（未配置时全部默认按 S30408 计算）。
- **默认流速能改吗？** 可以，在「管道」页顶部参数条修改后实时生效（默认流速：液体 `1.5 m/s`、气体 `15 m/s`、两相 `8 m/s`、泵入口 `0.8 m/s`，最大管径限制 `DN600`）。
- **不收敛模型能读吗？** 可以。数据照常入库，但会高亮标黄警告，并在统计与对比中默认隔离，同时完整保留报错原文。
</details>

---

## 工程口径与已知限制

使用本工具输出的数据用于课程设计、竞赛或工程参考时，请注意以下设计边界：

- **只读分析工具**：不执行闭环流程优化，不向 Aspen 写回任何参数。
- **管路选型边界**：当前支持最大管径至 **DN600**（超限流股将标注「需并联或专项设计」）；暂不计算管段沿程压降、外压稳定性及管道应力；两相流采用 `8 m/s` 保守流速，管径结果偏大，引用时需注明口径。
- **泵选型边界**：仅计算装置侧有效汽蚀余量 $\text{NPSH}_a$，泵必需汽蚀余量 $\text{NPSH}_r$ 需查阅厂商型谱；「建议电机」按常用电机功率档位表向上圆整，属于工程估算参考值，非强制国标；反向工况（轴功率 $\le 0$）不匹配电机。
- **测试覆盖边界**：泵参数已在 `MET` / `METCBAR` 单位集下实测，`ENG` 单位集按定义推导尚未经过真实模型验证；进口两相泵的拒算逻辑目前仅通过单元测试夹具验证。

---

## 项目结构与文档索引

```text
Casebook/
├── start.cmd            # Windows 一键启动脚本（带日志控制台）
├── requirements.txt     # Python 依赖清单 (pywin32, openpyxl, python-docx)
├── app/                 # 核心源码：aspen(COM只读)、store(SQLite)、design(纯计算引擎)、api(HTTP)、web(前端)
├── tools/               # 维护与批处理工具：字段探测、自测套件、历史重算、批量导出衡算报告、打包脚本
├── docs/                # 完整技术文档（安装、API、Aspen字段映射、选型与衡算规范）
├── samples/             # 离线演示用异常工况样本库
├── assets/              # 应用图标与静态资源
├── standards/           # [本地目录] 存放自备国标 PDF 原文（不入库）
├── model/               # [本地目录] 存放个人 Aspen 模型（不入库）
└── data/                # [本地目录] 存放 SQLite 快照、标准表 CSV 与等级配置（不入库）
```

### 核心文档导航
- **新手必读**：[`docs/install.md`](docs/install.md)（安装排错） · [`docs/README.md`](docs/README.md)（文档总索引）
- **计算口径**：[`docs/pipe-selection-plan.md`](docs/pipe-selection-plan.md)（管路选型） · [`docs/pump-selection-plan.md`](docs/pump-selection-plan.md)（选泵与 $\text{NPSH}_a$） · [`docs/balance-report.md`](docs/balance-report.md)（物能衡算报告）
- **规范与接口**：[`docs/pipe-class-codes.md`](docs/pipe-class-codes.md)（HG/T 20519.6 等级号） · [`docs/aspen-field-map.md`](docs/aspen-field-map.md)（Aspen 字段路径） · [`docs/api.md`](docs/api.md)（本地 HTTP API）
- **开发规范**：[`app/README.md`](app/README.md)（代码分层） · [`docs/ui-style.md`](docs/ui-style.md)（UI 规范） · [`docs/conventions.md`](docs/conventions.md)（项目约定）

---

## 参与贡献与联系

欢迎提交 Issue 反馈 Bug 或提出新功能建议，也欢迎直接发起 Pull Request：

1. 开发前请先阅读 [`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md) 与 [`app/README.md`](app/README.md)。
2. 提交前请确保通过离线回归测试：`python -m tools.selftest`。
3. 请勿提交任何 `.apw` 模型文件、国标 PDF 原文或本地 `data/` 数据库文件。
4. 如发现安全漏洞，请通过 [`.github/SECURITY.md`](.github/SECURITY.md) 私密反馈。

- **联系作者**：QQ `2655972249` / 微信 `User_DonkeyHeng`（建议优先通过 GitHub Issue 交流，长时间未回复再添加好友）。
- **开源协议**：本项目基于 [Apache License 2.0](LICENSE) 开源。

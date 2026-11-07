# aspen-insight

<img src="assets/app-icon_256.png" width="96" align="right" alt="aspen-insight 图标">

把 **Aspen Plus** 的仿真结果变成可查、可对比、可导出的本地数据工具。

打开已经跑好的 Aspen 模型,点一下「读取当前工况」,就能在本地网页里查看整条流程的流股、
设备、塔板剖面与连接图,做多工况对比,导出 Excel,或打包一份给 AI 阅读的结构化数据。

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Built with DeepSeek Harness](https://img.shields.io/badge/built%20with-DeepSeek%20Harness-4D6BFE)](https://www.deepseek.com/en/harness/)

> **⚠️ 关于本项目的两点说明**
>
> **1. 本项目依托于正版 Aspen Plus V15，项目中示例数据来源于本人独立完成的 Aspen 设计，由于本人能力有限，其中的数据可能存在较多错误以及不合理之处，但对于项目的适配是有利的，望各位多多担待。**
>
> **2. 本项目使用 [DeepSeek Harness（DSH）](https://www.deepseek.com/en/harness/) 完成** —— 代码、文档与整理均在 DSH 中协助生成。
> 这是一个个人项目,与 AspenTech 没有任何关联,使用前请自备正版 Aspen Plus 许可证。

---

## 它解决什么问题

Aspen Plus 的界面适合建模,不适合翻数据:想看某条流股的组成、对比两版设计的热负荷、
把塔的逐板剖面贴进报告,都得自己一格格点。而流程有错时,错误散落在收敛块、模块消息和
撕裂流里,很难一眼看出「这次结果到底能不能用」。

这个工具做的只有一件事:**把已经存在的仿真结果,读成干净、统一、可检索的数据**。

- 附着到**已经打开**的 Aspen 实例,只读,不启动新会话、不额外占许可证
- 一键把全流程读数存成本地 SQLite 快照,之后不连 Aspen 也能看
- 判定只看硬错误,不收敛的模型**照读但明确标记**
- 导出一份给人看的 Excel,和一份给 AI 读的文本包

## 功能

| 模块 | 能做什么 |
| --- | --- |
| 读取当前工况 | 任意页面可点。附着 → 读流股/设备/连接/塔板 → 存库 → 自动切换到新工况;百条流股约 15–40 s,有进度条和卡住提示 |
| 流股表 | 温度 / 压力 / 质量流量 / 摩尔流量 / 汽化率 / 体积流量,统一单位;点流股名看组成 |
| 流股组成 | 液相 x / 气相 y 摩尔分数与合计;首次点开时从 Aspen 现读并缓存,之后离线可看 |
| 设备与焓平衡 | 设备结果按「错误 → 告警 → 正常」排序,附 Aspen 原始提示;并做焓平衡自检(ΔH vs 热负荷/轴功率),区分通过、超差、无法校验、不校验 |
| 流程图 | 设备连接图,自动分层、缩放平移、点设备聚焦上下游两层;可导出 SVG / PNG 贴进报告 |
| 多工况对比 | 任选两个工况,按流股或按设备对比 A / B / 差值,并标出状态与焓平衡的变化 |
| 塔剖面 | 逐板温度、压力、液/气流量、热负荷,含曲线与联动表格 |
| 管路选型 | 按流股实时算管径与壁厚:相态 → 流速 → 内径 → 选 DN → 设计压力/温度 → 许用应力 → 壁厚 → 圆整(含负偏差)→ 管道等级号;附 V1–V9 校验(流速超区间、真空、超温、泵入口等) |
| 导出 | Excel 七张表(概要 / 流股 / 设备 / 连接 / 塔剖面 / 管道 / 管道计算书);或 AI 分析包(README + 多份 CSV + JSON 的 zip,含 `pipe_schedule.csv`) |
| 问题横幅 | 第一行直接给结论(「本次运行有 2 个模块报错,不建议用它算收率」),下面是明细,可收起 |

## 环境要求

| 项 | 要求 |
| --- | --- |
| 系统 | Windows 10 / 11(唯一支持平台) |
| Aspen | Aspen Plus **V15**(实测版本;其它版本未验证),需自备正版许可证 |
| Python | 3.10 及以上,依赖 `pywin32`、`openpyxl` |
| 会话 | 必须在**真实桌面会话**运行,Aspen 与本工具需要同权限级别 |
| 浏览器 | Edge / Chrome 等现代浏览器(前端零构建,不需要 Node) |

## 快速开始

```bash
git clone https://github.com/DonkeyHeng/aspen-insight.git
cd aspen-insight
python -m pip install -r requirements.txt
python -m app.api          # 打开 http://127.0.0.1:8000
```

Windows 下也可以直接双击 `start.cmd`(开控制台,方便看日志)。

然后:

1. **打开模型** —— 在 Aspen Plus 里加载 `.apw` 并完成一次计算
2. **点「读取当前工况」** —— 左上角蓝色按钮,等进度条走完
3. **看、比、导** —— 首页看概况,左栏切换流股 / 设备 / 流程,右上角导出

## 没有 Aspen 也能看:样本数据

仓库里带了一份**真实但失败**的工况快照,用来演示界面、验证判定逻辑,不需要 Aspen。
**注意:仓库不带 `data/cases.db`(本地库不入库),所以第一次打开是空库、首页会提示「还没有工况」,这是正常的。**

```bash
cp data/cases.db data/cases.local.db        # 有本地库时先备份
cp samples/case-23-error.db data/cases.db
python -m app.api
```

样本是 121 流股 / 77 模块 / 2178 条组成记录的异常工况(T0202 塔干涸、T0205 组分不收敛),
它正好能展示「失败工况长什么样、工具会怎么标」。细节见 [`samples/README.md`](samples/README.md)。

## 数据存在哪里

- 工况快照存在本地 `data/cases.db`(SQLite),不上传、不外传
- 单位统一换算为 ℃ / bar / kg·h⁻¹ / kW,原始值与原始单位会保留在导出物里
- 读不到的字段留空并在「单位诊断」里计数,不会静默丢弃
- 导出物就是普通文件:Excel 工作簿,或一个 zip

## 文档

| 文档 | 内容 |
| --- | --- |
| [`docs/aspen-field-map.md`](docs/aspen-field-map.md) | Aspen 的真实字段路径(给开发者/排错) |
| [`docs/pipe-class-codes.md`](docs/pipe-class-codes.md) | HG/T 20519.6 管道等级号规则(管路选型用) |
| [`docs/pipe-selection-plan.md`](docs/pipe-selection-plan.md) | 管路选型方案(口径与开工清单) |
| [`docs/pipe-standards-index.md`](docs/pipe-standards-index.md) | 管路选型要用的国标表格索引:表 1 / 表 4 / 表 B.1 在第几页、分页结构与数据校验记录 |
| [`docs/README.md`](docs/README.md) | 文档索引与阅读顺序 |
| [`docs/api.md`](docs/api.md) | 本地 HTTP 接口参考(排错 / 集成用) |
| [`docs/conventions.md`](docs/conventions.md) | 文件命名、目录与 README 规范(贡献者必读) |
| [`app/README.md`](app/README.md) / [`tools/README.md`](tools/README.md) | 代码分层 / 脚本用途 |

## 项目结构

```
aspen-insight/
  app/           应用代码:aspen(COM)、store(本地库)、api(HTTP)、web(零构建前端)
  tools/         调试与回算脚本:探测、基准、塔诊断、组成补读、回归自测
  docs/          字段地图与功能方案
  assets/        图标
  samples/       公开样本数据(可离线演示)
  standards/     标准 PDF 原文(不入库)
  model/         32 股精简模型与运行文件(不入库)
  model-full/    121 股全流程模型(不入库)
  data/          本地工况库与运行产物(不入库)
```

各目录都有独立 README,第一次看代码建议从 [`app/README.md`](app/README.md) 开始。

## 常见问题

**点了「读取当前工况」,提示看不到 Aspen?**
先确认:它是在真实桌面会话运行(远程/服务会话没有 COM)、和 Aspen 权限一致、
模型已经在 Aspen 里打开。仍不行就用 `tools/probe_aspen.py` 做一次诊断。

**为什么组成要点开流股才读?**
全量读是每条流股 36 次 COM 调用,121 条流股会从几秒变成几十秒。所以按需读、读完缓存。

**表格里有些单元格是 `—`?**
那不是模型没结果,而是单位没被换算表覆盖(多见于老模型的 ENG 单位集)。顶部「单位诊断」会给出计数。

**不收敛的模型能读吗?**
能。读数照存,但会标黄并禁止进入统计对比;状态码与模块报错原文都会列出。

**管路选型用的国标表格为什么不在仓库里?**
那三张表来自 GB/T 17395 与 GB/T 20801.1 的表格原文,按项目约定**标准原文与派生数据都不入库**。
所以仓库里是空的:管道页会显示「标准表未录入」并列出缺哪张,不会算出一张假表。
录入说明与页码索引见 [`docs/pipe-standards-index.md`](docs/pipe-standards-index.md)。

**管路选型会自动判断该用什么材质吗?**
不会,也不该。腐蚀取决于浓度、温度、含水量、氯离子 ppm 等因素,不是靠组分清单能定的;
工具只按你给的**管道等级表**(`data/config/piping-classes.json`,示例见 `samples/`)套用材质与腐蚀裕量,
表里没覆盖的流股会明确标出来。

**会改动我的模型吗?**
不会。工具只读取节点值,不调用 Run / Reinit,也不会保存模型。

**支持 Linux / Mac 吗?**
不支持。Aspen 的 COM 接口只存在于 Windows。

## 已知限制

- 只读工具,不做闭环优化,也不写回 Aspen
- 管路选型本期只到 **DN600**(更大的标"需并联/专项设计");不计算管长压降、外压稳定与管道应力
- 材质与腐蚀裕量来自**用户定义的管道等级表**,工具不判断介质耐蚀性
- 塔与反应器不参与焓平衡校验(这类设备的能量关系不适用简单加和)
- 只在 Aspen Plus V15 上实测过,其它版本行为未验证
- 个人项目,非官方产品;Aspen Plus 是 Aspen Technology, Inc. 的商标

## 参与贡献

欢迎 issue 与 pull request。动手前建议先看 [`CONTRIBUTING.md`](CONTRIBUTING.md),以及:

1. [`app/README.md`](app/README.md) —— 代码分层与「改判定 / 单位 / 焓平衡必须先跑自测」的约定
2. [`tools/README.md`](tools/README.md) —— `python -m tools.selftest` 是不需要 Aspen 的回归自测
3. 请不要提交模型文件、标准 PDF 原文或本地工况库(已在 `.gitignore` 中)
4. 发现安全问题请走 [`SECURITY.md`](SECURITY.md) 的私密渠道,不要开公开 issue

## 许可证

本项目采用 [Apache License 2.0](LICENSE) 许可,可自由使用、修改与再分发,
条件是保留版权与许可声明(全文见 [`LICENSE`](LICENSE))。

## 致谢

- **DeepSeek Harness** —— 本项目的代码、文档与整理工作均在 DSH 中完成
- Aspen Plus 及其相关名称是 Aspen Technology, Inc. 的商标;本项目与该公司无关联,也不包含其任何软件

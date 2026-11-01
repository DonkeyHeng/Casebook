# aspen-insight

Aspen Plus 数据读取与分析工具。通过 COM 附着到已运行的 Aspen Plus 实例,读取全流程数据,
本地建工况库,并导出便于 AI 分析的结构化数据包。

> 状态:第一期核心打通 —— **网页里点「读取当前工况」→ 自动入库 → 看表/对比/看塔剖面 → 导出**全链路可用(2026-11-01)。
> 真实字段路径见 [`docs/aspen-field-map.md`](docs/aspen-field-map.md),不要靠猜。

## 定位

| 项 | 结论 |
| --- | --- |
| 产品定位 | 数据分析工具(不做闭环优化) |
| 连接方式 | **附着(attach)** 到已打开并收敛的 Aspen 实例,零启动开销、不额外占许可证 |
| 模型规模目标 | 200–1000 个流股(需懒加载 + 后台加载 + 进度提示) |
| 用户 | 单人本机使用,无权限体系 |
| 第一期范围 | 流股总表 + 设备结果表 |
| 二期范围 | 塔板剖面、多工况对比、公用工程(介质等级 + 成本) |
| 单位 | 统一换算为固定单位(℃ / bar / kg·h⁻¹ / kW) |
| 存储 | 本地 SQLite 工况快照(名称、时间、收敛状态) |
| 导出 | 「AI 分析包」= Markdown 说明 + 精简指标 CSV + 完整数据 CSV |
| 应用形态 | 本地网页应用;Python 后端 + 零构建原生 HTML + ECharts;可打包 exe |

## 功能

| 页面 | 内容 |
| --- | --- |
| 读取当前工况 | 右上角蓝色按钮。点一下 = 附着到已打开的 Aspen、读全流程、存库、自动切到新工况。<br>读取约 10–30 s(1000 流股约 76 s),过程中显示阶段和进度条,不卡页面;<br>失败时直接在页面上给中文提示(Aspen 没开 / 权限不一致 / 在沙箱里跑) |
| 流股 | 全部流股的温度/压力/质量流量/摩尔流量/汽化率/体积流量(已统一单位) |
| 设备 | 全部设备,按"错误 → 告警 → 正常"排序,带 Aspen 原始提示;塔名可点开剖面 |
| 对比 | 任选两个工况,逐流股显示 A 值 / B 值 / 差值;入口是工具栏右上角的 **⇄** 图标,<br>进对比页后同一行变成「基础 / 对比」下拉框——整行高度不变,切换页签不跳动 |
| 塔剖面 | 右侧抽屉:逐板温度、压力、液/气流量、热负荷,含温度曲线和流量曲线;<br>**鼠标移到曲线上任意位置 → 浮出该级全部数值**,两张图共用一条准线,下方表格同步高亮对应行 |
| 导出 Excel | 右上角「导出 Excel」→ 一个工作簿四张表:概要 / 流股 / 设备 / 塔剖面(给人看) |
| AI 分析包 | 右上角「AI 分析包」→ 一个 zip,里面是 `README.md`(数据字典 + 这次运行的状态)、<br>`metrics.csv`(精简指标)、`streams.csv` / `blocks.csv` / `column_profiles.csv`(完整数据)、`case.json`。<br>纯文本、空值留空、单位已归一、原始值与原始单位保留(给 AI 读) |
| 退出 | 右上角「退出」:二次确认后停掉后台服务并关闭窗口(默认用浏览器"应用窗口"模式打开,所以关得掉);<br>也可以用桌面 `aspen-insight 停止` 快捷方式 |
| 窗口记忆 | 记住上次的窗口大小、位置、是否最大化,下次打开原样恢复;点窗口右上角 ✕ 会自动停服务 |
| 问题横幅 | 顶部红色横幅列出该工况的问题原因(状态码 + 模块报错原文),<br>右边缘有「收起 / 展开」按钮;每次打开默认展开;对比页也保留这块高度,切页签顶部不跳 |

> 有问题的工况会在顶部红色横幅列出原因(CVSTAT、模块报错原文、撕裂流误差)。

## 关键约束(已实测)

- Aspen Plus V15 的 COM 接口注册完整:`Apwn.Document` → CLSID `{8EDFE38B-7513-45D6-9A63-B161FCC3832E}`,
  另有版本化 ProgID `Apwn.Document.41.0`(`41.0` = V15)与只读入口 `Apwn.ReadOnlyComposite.41.0`。
- **附着必须走文件 moniker**:手工打开的 Aspen 只把文档注册成 `...\model.apw`,
  `GetActiveObject("Apwn.Document")` 会报 `MK_E_UNAVAILABLE`。正确写法:
  `win32com.client.GetObject(r"模型路径.apw")`。
- 必须在**真实桌面会话**执行:DSH 沙箱内 ROT 为空,COM 全部不可用。
- 必须以**相同权限级别**运行:若 Aspen 以管理员身份启动,普通权限进程无法附加。
- 流股结果字段是 `RES_TEMP` / `RES_PRES` / `RES_MASSFLOW` / `RES_MOLEFLOW` / `RES_VFRAC`,
  `TEMP` / `MASSFLOW` 这些是 0/1 开关,不是数值。
- 单位不统一(加热器 `cal/sec`、泵 `kW`),必须按节点 `UnitString` 换算。
- **模块级报错要看 `\Data\Blocks\<设备>\Output\BLKSTAT`(0 正常 / 1 错误 / 2 告警)和 `BLKMSG`**;
  Run-Status 里没有完整错误文本,不要指望从树里读到一句现成的结论。
- 撕裂流误差小 ≠ 流程可用:曾实测到 6 条撕裂流误差都在 1e-14~1e-6,
  但两个塔仍报"干涸""组分平衡不收敛"。
- **不收敛的数据照读,但必须标记**:收敛结果(绿)/ 未收敛有值(黄,禁止进入统计对比)/ 读取失败(红)。
- 前端不使用需要 Node 构建的框架,保证「打包 exe」链路只有一步。

## 目录结构

```
aspen-insight/
  probe_aspen.py          COM 只读探测脚本(诊断入口)
  app/
    cli.py                命令行:读当前工况入库、列工况、看明细
    api/
      server.py           HTTP 接口(标准库,零依赖)
      read_job.py         网页「读取当前工况」的后台任务(进度/状态机)
      aipack.py           AI 分析包生成(Markdown + CSV + JSON → zip)
      window.py           窗口几何记忆(ctypes)
    web/                  原生 HTML + CSS + JS 表格(零构建)
    aspen/
      connect.py          附着(ROT → GetObject),失败给人话提示
      units.py            单位归一(C/bar/kg·h⁻¹/kW…)
      reader.py           读流股/组成/设备/收敛,并做三档判定
    store/
      db.py               SQLite 工况库(cases/streams/stream_components/blocks/column_stages)
  tools/
    bench.py              性能基准(测 COM 调用成本与规模曲线)
    column_diag.py        塔逐板剖面 + 异常标记(只标记,不给修复建议)
    stop.py               按端口停止服务
  docs/aspen-field-map.md 真实字段路径地图(实测)
  model/                  Aspen 模型与它的运行文件(见 model/README.md)
  data/cases.db           本地工况库(运行时生成,不入库)
  probe_out/              探测报告输出
```

> **模型统一放 `model/`**:Aspen 会把 `_<runid>.*` 运行中间文件写在 `.apw` 旁边,
> 模型进子目录后,根目录就不会被这些文件淹掉。从这里打开模型,不要另存到根目录。

## 运行

**最省事:双击桌面上的 `aspen-insight` 快捷方式** —— 后台起服务(无黑窗口)并自动打开浏览器;
打开 Aspen、加载 `.apw`,然后在网页右上角点**「读取当前工况」**。
关服务用桌面上的 `aspen-insight 停止` 快捷方式,或页面右上角的「退出」。

快捷方式直接指向 `pythonw.exe`(GUI 版 Python),所以:

- 不会弹"无法验证发布者"的安全警告(不经过 `.cmd` 脚本宿主)
- 不会留一个终端窗口在桌面上
- 服务日志写到 `data/server.log`(出问题时看这里)
- 重复双击只会再打开一次网页,不会起第二个服务

手动/调试方式(`start.cmd` 会开一个控制台窗口,方便看实时输出):

```bash
python -m pip install -r requirements.txt

# 1) 读工况:网页按钮已能完成,命令行只是备选/排错用
python -m app.cli --list        # 看现在开着哪些模型
python -m app.cli --read        # 读取并存库,打印摘要
python -m app.cli --cases       # 列出已存工况
python -m app.cli --show 1      # 看某个工况的明细

# 2) 起网页看表(调试时用,能看到请求日志)
python -m app.api               # 打开 http://127.0.0.1:8000

# 3) 工具
python -m tools.bench                  # 性能基准
python -m tools.column_diag T0202      # 塔逐板剖面 + 异常标记
python tools/stop.py 8000              # 停止服务(等价于桌面"停止"快捷方式)
python probe_aspen.py --attach --out probe_out   # 完整探测报告(排错用)
```

> COM 必须在真实桌面会话执行;DSH 沙箱里 ROT 为空,会误报"Aspen 没开"。

### 接口(给排错用)

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/api/read` | 起一次后台读取;已有任务在跑时返回 409 |
| GET | `/api/read/status` | 读取进度/结果(前端每 0.6 s 轮询) |
| GET | `/api/cases/{id}/export.xlsx` | 导出 Excel(给人看) |
| GET | `/api/cases/{id}/export.ai.zip` | 导出 AI 分析包(给 AI 读) |

工况库默认写到 `data/cases.db`(可用 `--db` 指定)。
探测报告写入 `probe_out/probe_report.txt` 与 `.json`。

## 免责与边界

- 探测与应用默认**只读**,除显式指定外不调用 `Run2` / `Reinit`,不改动模型。
- 无 Aspen 环境的机器无法运行本工具(不做演示模式)。

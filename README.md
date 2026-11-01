# aspen-insight

Aspen Plus 数据读取与分析工具。通过 COM 附着到已运行的 Aspen Plus 实例,读取全流程数据,
本地建工况库,并导出便于 AI 分析的结构化数据包。

> 状态:第一期核心打通 —— **点「读取当前工况」→ 自动入库 → 看表/对比/塔剖面/流程图 → 焓平衡自检 → 导出**全链路可用(2026-11-01)。
> 真实字段路径见 [`docs/aspen-field-map.md`](docs/aspen-field-map.md),不要靠猜。

## 定位

| 项 | 结论 |
| --- | --- |
| 产品定位 | 数据分析工具(不做闭环优化) |
| 连接方式 | **附着(attach)** 到已打开并收敛的 Aspen 实例,零启动开销、不额外占许可证 |
| 模型规模 | **实测 90–128 流股**(6 份样本模型:90/98/105/116/128,13.8–19.1 s);<br>更大规模未验证,按线性外推 500 流股 ≈ 60–75 s。有进度条 + 卡住提示,规模变大是"变慢"不是"崩掉" |
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
| 首页 | 左栏第一项:工况信息、收敛横幅、规模(流股 / 设备 / 连接 / 塔)、**焓平衡四态**、异常模块;<br>导出 Excel / AI 分析包 / 删除工况也在这一页 |
| 读取当前工况 | 左栏顶部蓝色按钮,**任何页面都能点**。点一下 = 附着到已打开的 Aspen、读全流程、存库、自动切到新工况。<br>读取约 10–30 s(1000 流股约 76 s),进度条里会显示**正在读哪个模型文件**;<br>失败时直接在页面上给中文提示(Aspen 没开 / 权限不一致 / 在沙箱里跑) |
| 单位诊断 | 读完会告诉你**有多少字段因为单位不认识被留空**(老模型可能是 ENG:℉/psia/Btu·h⁻¹),<br>这类 `—` 不是模型没结果,是换算表没覆盖;有缺口时提示不自动消失 |
| 流股 | 全部流股的温度/压力/质量流量/摩尔流量/汽化率/体积流量(已统一单位);**点流股名 → 看组成** |
| 流股组成 | 右侧抽屉:每个组分的液相 x / 气相 y 摩尔分数 + 合计(正常应 ≈ 1)。<br>没读过时从当前打开的 Aspen 现读这一条(实测 0.26 s)并存入工况,之后直接取、不再连 Aspen |
| 设备 | 全部设备,按"错误 → 告警 → 正常"排序,带 Aspen 原始提示;塔名可点开剖面;<br>**焓平衡四列**:ΔH / 参照 / 焓平衡(通过·超差·无法校验·不校验)/ 平衡说明 |
| 焓平衡自检 | `ΔH = Σ出 − Σ入`(流股能量流)对设备热负荷 / 轴功率:Heater 1%、绝热设备 0.5%、压缩机和泵按效率比、换热器按冷侧;<br>塔和反应器不校验。口径在 `app/aspen/balance.py`,改规则后 `python -m tools.repair_cases --apply` 即可回算 |
| 流程 | 设备连接图:全图自动分层(链路太长自动折行),滚轮缩放 / 拖动平移 / 双击适应窗口;<br>**点设备聚焦上下游 2 层**并弹出该设备的进出流股与焓平衡;<br>`#flow/设备名` 可直达某台设备;入口是左栏「流程」 |
| 对比 | 任选两个工况,左上角「流股 / 设备」开关切换对比对象(两张表都显示 A 值 / B 值 / 差值);<br>设备对比看热负荷、功率、级数、顶温、底温,外加状态列(变了就显示 `正常 → 错误`);<br>设备名固定在左侧,列多横向滚也不会跟丢;入口是工具栏右上角的 **⇄** 图标 |
| 塔剖面 | 右侧抽屉:逐板温度、压力、液/气流量、热负荷,含温度曲线和流量曲线;<br>**鼠标移到曲线上任意位置 → 浮出该级全部数值**,两张图共用一条准线,下方表格同步高亮对应行 |
| 导出 Excel | 首页右上角「导出 Excel」→ 一个工作簿五张表:概要 / 流股(含焓)/ 设备(含 ΔH)/ 连接 / 塔剖面(给人看) |
| AI 分析包 | 首页右上角「AI 分析包」→ 一个 zip,里面是 `README.md`(数据字典 + 这次运行的状态)、<br>`metrics.csv`(精简指标,含「焓平衡」一类)、`streams.csv` / `blocks.csv` / `connections.csv` /<br>`column_profiles.csv` / `composition.csv`(完整数据;后两个没有就不生成)、`case.json`。<br>纯文本、空值留空、单位已归一、原始值与原始单位保留(给 AI 读) |
| 删除工况 | 首页右上角红色「删除工况」:二次确认框里写明模型名 / 读取时间 / 规模,避免删错;<br>明细(流股/设备/连接/塔剖面/组成)一起删,删完自动切到剩下的最新工况 |
| 退出 | 左栏底部「退出」:二次确认后停掉后台服务并关闭窗口(默认用浏览器"应用窗口"模式打开,所以关得掉);<br>也可以用桌面 `aspen-insight 停止` 快捷方式 |
| 窗口记忆 | 记住上次的窗口大小、位置、是否最大化,下次打开原样恢复;点窗口右上角 ✕ 会自动停服务 |
| 问题横幅 | 第一行就是**结论**:"本次运行有 2 个模块报错 —— 不建议用它算收率 / 能耗 / 做工况对比";<br>下面才是明细(状态码 + 模块报错原文),右边缘「收起 / 展开」只收起明细、结论仍在;对比页也保留这块高度,切页签顶部不跳 |
| 卡住提示 | 读取超过 2 分钟还没结束,进度条变成琥珀色并提示:可能 Aspen 正忙 / 弹了对话框 / 许可证排队 |

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
- 判定只看**硬错误**:模块 `BLKSTAT=1`、收敛块 `CVSTAT≠0`、撕裂流超容差。
  `UOSSTAT` / `PER_ERROR` 非 0 只说明"有东西非零",**区分不了告警和错误**,
  不能据此判"有错误"(实测:一个全是告警的样本模型,这两个码同样是 1)。
- 撕裂流误差小 ≠ 流程可用:曾实测到 6 条撕裂流误差都在 1e-14~1e-6,
  但两个塔仍报"干涸""组分平衡不收敛"。
- **不收敛的数据照读,但必须标记**:收敛结果(绿)/ 未收敛有值(黄,禁止进入统计对比)/ 读取失败(红)。
- 组成**按需读**:18 组分 × 2 相 × 每条流股 = 36 次 COM 调用,全量会让 121 条流股从 9 s 变 41 s。
  所以只在点开时读一条,并且读之前核对两件事:**当前打开的模型就是这个工况的来源模型**、
  **运行 ID 还是同一次运行**(模型重跑过就不给,免得和旧快照的温度流量混在一起算)。
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
      composition.py      流股组成按需读(模型/运行 ID 校验 + 缓存)
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
    make_icon.py          从源图生成 .ico(圆形抠底 + 7 个尺寸)
    repair_cases.py       按最新规则/单位表重算历史工况(不用回 Aspen 重读)
    stop.py               按端口停止服务
  assets/app-icon.ico    图标源件(favicon 用它;桌面快捷方式用部署到 %LOCALAPPDATA% 的副本)
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

## 图标

<img src="assets/app-icon_256.png" width="120" alt="aspen-insight 图标">

图标用在两处:**桌面快捷方式**和**网页 favicon**(所以浏览器"应用窗口"在任务栏里也是这张脸)。

注意它们用的是**两份拷贝**:

| 谁用 | 路径 |
| --- | --- |
| 桌面快捷方式 | `%LOCALAPPDATA%\aspen-insight\app-icon.ico`(**必须放在项目目录之外**) |
| 网页 favicon | 仓库里的 `assets/app-icon.ico` |

为什么快捷方式不能指项目目录:实测同一个文件放在项目目录下,资源管理器取不到图标,
桌面会显示成通用的白色文件图标;复制到用户目录后立刻正常。

源图是根目录的 `Gemini_Generated_Image_pjxeyapjxeyapjxe.jpg`(2048²,白底圆形徽章)。改图标就重跑:

```bash
python tools/make_icon.py "Gemini_Generated_Image_pjxeyapjxeyapjxe.jpg" assets/app-icon.ico
```

脚本做三件事:按**圆的几何**把圆外白底抠成透明(不做全局"白→透明",否则徽章上
"DEEPSEEK / ASPEN PLUS"的白字会被一起镂空)、生成 16/24/32/48/64/128 六个尺寸、
把成品部署一份到 `%LOCALAPPDATA%\aspen-insight\`。

> 快捷方式里存的是**绝对路径**。项目搬家或部署副本被删,图标会变回默认样式,
> 重跑上面的命令、再把 `IconLocation` 指过去即可
> (`WScript.Shell` → `CreateShortcut` → `.Save()`)。

### 接口(给排错用)

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/api/read` | 起一次后台读取;已有任务在跑时返回 409 |
| GET | `/api/read/status` | 读取进度/结果(前端每 0.6 s 轮询) |
| GET | `/api/compare?base=1&other=2` | 逐流股 + 逐设备对比两个工况(A / B / 差值 / 状态变化) |
| GET | `/api/cases/{id}/connections` | 设备 ↔ 流股连接关系(方向 / 换热器冷热侧 / 塔进料板 / 来源) |
| GET | `/api/cases/{id}/flowsheet` | 流程图数据(节点 = 设备、边 = 设备间流股、边界进料 / 产品) |
| DELETE | `/api/cases/{id}` | 删除工况及其全部明细(流股/设备/连接/塔剖面/组成) |
| GET | `/api/cases/{id}/streams/{流股}/composition` | 单条流股的组成;没存过就现从 Aspen 读一条并缓存,读不了时返回 409 + 原因 |
| GET | `/api/cases/{id}/export.xlsx` | 导出 Excel(给人看) |
| GET | `/api/cases/{id}/export.ai.zip` | 导出 AI 分析包(给 AI 读) |

工况库默认写到 `data/cases.db`(可用 `--db` 指定)。
探测报告写入 `probe_out/probe_report.txt` 与 `.json`。

## 免责与边界

- 探测与应用默认**只读**,除显式指定外不调用 `Run2` / `Reinit`,不改动模型。
- 无 Aspen 环境的机器无法运行本工具(不做演示模式)。

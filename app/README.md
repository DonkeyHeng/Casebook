# app/ — 应用代码

代码按「离 Aspen 多远」分层,各层职责单一,便于阅读和修改。

```
app/
  version.py 版本号与 GitHub 仓库常量的唯一来源(运行期 / 发布包共用;exe 内没有 .git)
  paths.py 运行期基准目录:只读资源(app/web、assets)与可写数据(data/)分开;
           打包成 exe 后前者在临时解包目录,后者在 exe 旁边
  values.py  与层无关的数值口径:p_v 有效性、摩尔密度→质量密度、电机档位查表
  component_names.py 组分中文名:CAS 号 → 中文名(纯查表,aspen / design / 导出共用)
  aspen/   与 Aspen COM 打交道的唯一地方
    connect.py    附着已打开的 Aspen 实例,把 COM 异常翻成可读的提示
    reader.py     读流股 / 组成 / 设备 / 收敛 / 塔剖面;judge() 是唯一的判定入口
    units.py      单位归一(℃ / bar / kg·h⁻¹ / kW);不认识的单位留空并计数
    balance.py    焓平衡自检 ΔH vs 热负荷 / 轴功率(纯函数,可回算)
  store/   只碰本地数据,不连 Aspen
    db.py         SQLite 工况库:cases / streams / stream_components / blocks / column_stages / connections,
                  以及管路与泵的持久化记录 pipe_run / pipe_setting / pump_selection / pump_design
    flowsheet.py  连接关系 → 流程图数据(纯函数)
    impact.py     报错设备 → 受影响流股(纯函数,分「不可用 / 仅告警」)
    sections_config.py 工段(界区)边界配置读写 data/config/sections.json + 结构校验
  design/  计算与取数适配:纯计算不碰 Aspen;只有 pump_line_context 按需读库,不写库
    sections.py    工段(界区)判定:设备名段号推草稿、配置归一化、草稿与确认结果合并
    stream_table.py 物料平衡宽表:流股做列、属性与组分做行(工段级 + 全流程 + 年量)
    mass_balance.py 物料衡算:界区 Σ进界=Σ出界 + 逐设备 Σ进口=Σ出口(纯函数)
    mole_balance.py 摩尔守恒:无反应设备的 Σ摩尔进口=Σ摩尔出口,补质量守恒看不见的
                   流量口径错(纯函数,反应器与塔不判)
    energy_report.py 能量衡算表与全流程汇总(ΔH 由调用方注入 compute() 的结果)
    block_family.py 设备家族:把 Aspen 几十种块类型收敛成 9 个页签
    model_identity.py 模型身份:内容哈希优先,文件名兜底(选型台账按模型去重靠它)
    hydraulics.py  管路阻力:流速 / Re / λ / Σhf(供选泵的设计扬程用)
    pump.py        泵的派生量:密度、自算扬程、NPSHₐ、建议电机功率
    pump_selection.py 选型台账的实时计算层:只算不存,台账只放人工决定
    pump_line.py   泵两侧管段的水力计算:管径 / 管长 / Σζ → u、Re、λ、Σhf,
                   缺项返回 None;两侧分别算,修正 NPSHₐ 只扣吸入段
    pump_line_context.py 把 pipe_run、管道页选型结果和泵读数拼成同一份泵管路视图,
                   页面、Excel、AI 包与自测共用,避免同一根管子两个数
    pump_prompt.py 选泵提示词正文(唯一来源,三个出口都读它)
    pipe/         管路选型(改一处要一起看的三块,放在一个包里)
      calc.py     十步选型:相态 → 流速 → 内径 → 选 DN → 设计压力/温度 → [σ] → 壁厚 →
                  圆整(含负偏差)→ 等级号;外加校验 V1–V15(V14 预留)
      data.py     标准数据表(表 8/9 壁厚偏差、Y 值、PN/材质代号、DN→外径)+ 运行时加载
                  表 1 / 表 4 / 表 B.1 的 CSV(`data/preview/std/docx/`,不入库)+ 管道等级表(用户配置)
      prompt.py   「复制填写提示词」的正文:文件名/必需列取自 STD_FILES,
                  示例行取自 samples/pipe-standards-template/(唯一来源)
  export/  数据 → 文件字节(.docx / .xlsx / zip);不认 HTTP,网页与命令行共用同一套
    report.py         衡算报告 .docx(物料 + 能量,三线表;python-docx 懒导入)
    pipe_schedule.py  管道选型一览表 .docx(复用 report 的封面 / 页眉 / 三线表骨架)
    family_xlsx.py    按设备分类导出 .xlsx(设备页「导出 Excel」)
    workbook.py       整本 Excel(概览 / 流股 / 设备 / 泵 / 连接 / 塔剖面 / 管道计算书)
    aipack.py         分析数据包(Markdown + CSV + JSON → zip)
  api/     HTTP 服务(标准库,零第三方 Web 框架)
    server.py      HTTP 外壳:安全闸、响应、静态文件、查表分发、启停
    router.py      路由表原语(Route / match)
    routes/        按域拆开的处理逻辑,一个域一个文件
      __init__.py  两张路由表:ROUTES / CASE_ROUTES —— 接口清单的唯一来源
      cases.py     工况:列表、明细、流股 / 设备 / 拓扑、工段、组成、删除
      lines.py     两侧管段:按流股读写 pipe_run,保存时带版本号防覆盖
      pumps.py     泵:设计扬程、水力、p_v、选型台账
      pipes.py     管路:一览表、标准表导入、管道等级表、模板下载
      exports.py   Excel / AI 包 / 管道 Word / 衡算报告
      meta.py      健康检查、版本、读取任务、工况对比、示例库
    serialize.py   行转换与标准表状态(server 与 routes 共用)
    errors.py      请求级异常(BadBody / BadQuery)
    update_check.py 「检查更新」的版本比对(HEAD 302 → tag);唯一的出站请求,不下载不安装
    read_job.py   「读取当前工况」后台任务(状态机 + 进度 + 卡住提示)
    composition.py 组成按需读(先校验模型与运行 ID,避免旧快照混入)
    window.py     窗口几何记忆(ctypes)
  web/     零构建前端(原生 ES Module):index.html + static/
    app.js       入口:事件绑定与 init;页面实现在 views/
    pipe.js      管道页(单独一个模块:进页才拉数据)
    lib/core.js  请求封装与数字格式化(全部页面共用)
    lib/store.js 共享页面状态(state 是唯一数据源,字段归属表见文件头)
    lib/ui.js    界面外壳公用件:主题、提示条、徽章、判定条
    views/table.js       数据页表格:流股 / 设备 / 对比 / 家族页签 / 泵列
    views/pump.js        泵参数悬浮窗:详细参数、设计扬程 / 两侧管段、选型台账
    views/composition.js 塔剖面详情与流股组成 / CAS 编辑
    views/column.js      塔剖面图(曲线)
    views/report.js      衡算报告页
    views/read.js        读取进度:启动、轮询、完成与错误
    views/flow.js        流程图:布局、缩放、聚焦、导出
    views/shell.js       应用外壳:工况载入、页面路由、首页、更新检查
    style.css    样式
```

`api/routes/` 按 URL 前缀分域,加接口是"表里加一行 + 对应域加一个方法",不用在
三个 `do_*` 里找分支;表里写了不存在的方法名,自测的 `test_routes()` 会直接报错。

`export/` 单独一层,是因为导出逻辑既被网页路由调用,也被命令行工具调用
(`tools/balance_report.py`、`tools/make_pipe_schedule.py`):放在 `api/` 里会让
命令行为了生成一份 Word 把整个 HTTP 服务拖进来。

`design/` 的纯计算模块不依赖 `aspen/` 与 `store/`:它们只接收「流股数值 + 配置」并算出一张表。
例外是 `design/pump_line_context.py`:它在函数内按需 import `store.db`,把 `pipe_run` 与泵读数拼成
视图,但不写库、不碰 Aspen。衡算相关的纯函数连 `from app.aspen import balance` 都不写 ——
`app/aspen/__init__.py` 会把 `connect.py` 的 `import pythoncom` 拖进来,那样 `design/` 就不再能
离线跑了;所以 `compute()` 的结果由调用方(api 层 / 工具)当参数传进来。

反过来 `aspen/` 也不依赖 `design/`:读取层要用的那几条数值口径(p_v 有效性、
摩尔密度→质量密度、电机档位)放在与层无关的 `app/values.py`,两边都只依赖它。
依赖方向是单向的:`aspen → values`、`design → values`、`store → design`、
`export → design/store`、`api → 全部`。
数据没录全时它会直接报 `TablesNotReady`,而不是拿 0 算出一张看起来正常的假表。

## 跑起来

```bash
python -m app.api            # 起服务,浏览器开 http://127.0.0.1:8000
python -m app.cli --cases    # 命令行看已存工况(不用开网页)
```

前端是原生 HTML/JS/CSS:**改完刷新即生效**;Python 改完**必须重启服务**。

## 修改前的注意事项

1. **判定**只在 `aspen/reader.py` 的 `judge()` 里改;改完先跑
   `python -m tools.selftest --db data/cases.db`。
2. **单位**换算表在 `aspen/units.py`;新增单位同样先跑自测。
3. **焓平衡**口径在 `aspen/balance.py`(纯函数);同样先跑自测。

判定 / 单位 / 焓平衡的口径变化会改历史工况的结论,改完可以用
`python -m tools.repair_cases --apply` 按新规则重算库里的工况,不需要回 Aspen 重读。

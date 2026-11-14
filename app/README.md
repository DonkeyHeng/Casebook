# app/ — 应用代码

代码按「离 Aspen 多远」分层,各层职责单一,便于阅读和修改。

```
app/
  paths.py 运行期基准目录:只读资源(app/web、assets)与可写数据(data/)分开;
           打包成 exe 后前者在临时解包目录,后者在 exe 旁边
  aspen/   与 Aspen COM 打交道的唯一地方
    connect.py    附着已打开的 Aspen 实例,把 COM 异常翻成可读的提示
    reader.py     读流股 / 组成 / 设备 / 收敛 / 塔剖面;judge() 是唯一的判定入口
    units.py      单位归一(℃ / bar / kg·h⁻¹ / kW);不认识的单位留空并计数
    balance.py    焓平衡自检 ΔH vs 热负荷 / 轴功率(纯函数,可回算)
  store/   只碰本地数据,不连 Aspen
    db.py         SQLite 工况库:cases / streams / stream_components / blocks / column_stages / connections
    flowsheet.py  连接关系 → 流程图数据(纯函数)
    impact.py     报错设备 → 受影响流股(纯函数,分「不可用 / 仅告警」)
  api/     HTTP 服务(标准库,零第三方 Web 框架)
    server.py     路由 + 静态文件 + 导出
    read_job.py   「读取当前工况」后台任务(状态机 + 进度 + 卡住提示)
    composition.py 组成按需读(先校验模型与运行 ID,避免旧快照混入)
    aipack.py     分析数据包(Markdown + CSV + JSON → zip)
    report_export.py 衡算报告 .docx(物料 + 能量,三线表;python-docx 懒导入)
    report_config.py 工段(界区)边界配置读写 data/config/sections.json
    window.py     窗口几何记忆(ctypes)
  design/  纯计算:不碰 Aspen、不读写库
    sections.py    工段(界区)判定:设备名段号推草稿、配置归一化、草稿与确认结果合并
    mass_balance.py 物料衡算:界区 Σ进界=Σ出界 + 逐设备 Σ进口=Σ出口(纯函数)
    energy_report.py 能量衡算表与全流程汇总(ΔH 由调用方注入 compute() 的结果)
    pipe_data.py  标准数据表(表 8/9 壁厚偏差、Y 值、PN/材质代号、DN→外径)+ 运行时加载
                  表 1 / 表 4 / 表 B.1 的 CSV(`data/preview/std/docx/`,不入库)+ 管道等级表(用户配置)
    pipe.py       十步选型:相态 → 流速 → 内径 → 选 DN → 设计压力/温度 → [σ] → 壁厚 →
                  圆整(含负偏差)→ 等级号;外加校验 V1–V9
  web/     零构建前端:index.html + static/{app.js,pipe.js,style.css}
```

`design/` 不依赖 `aspen/` 与 `store/`:它只接收「流股数值 + 配置」并算出一张表。
衡算相关的纯函数连 `from app.aspen import balance` 都不写 —— `app/aspen/__init__.py`
会把 `connect.py` 的 `import pythoncom` 拖进来,那样 `design/` 就不再能离线跑了;
所以 `compute()` 的结果由调用方(api 层 / 工具)当参数传进来。
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

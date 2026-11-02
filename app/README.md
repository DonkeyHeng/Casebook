# app/ — 应用代码

按「离 Aspen 多远」分层:改任何一层都不用碰其他层。

```
app/
  aspen/   与 Aspen COM 打交道的唯一地方
    connect.py    附着(ROT → GetObject),把 COM 异常翻成人话
    reader.py     读流股/组成/设备/收敛;judge() 是唯一的判定入口
    units.py      单位归一(℃ / bar / kg·h⁻¹ / kW);不认识的单位留空并计数
    balance.py    焓平衡自检 ΔH vs 热负荷/轴功率(纯函数,可回算)
  store/   只碰本地数据,不连 Aspen
    db.py         SQLite 工况库:cases / streams / stream_components / blocks / column_stages / connections
    flowsheet.py  连接关系 → 流程图数据(纯函数)
    impact.py     报错设备 → 受影响流股(纯函数,分「不可用 / 仅告警」)
  api/     HTTP 服务(标准库,零第三方 Web 框架)
    server.py     路由 + 静态文件 + 导出
    read_job.py   「读取当前工况」后台任务(状态机 + 进度 + 卡住提示)
    composition.py 组成按需读(先校验模型与运行 ID,避免旧快照串味)
    aipack.py     AI 分析包(Markdown + CSV + JSON → zip)
    window.py     窗口几何记忆(ctypes)
  web/     零构建前端:index.html + static/{app.js,style.css}
```

## 改代码的三条硬规则

1. **判定**只在 `aspen/reader.py` 的 `judge()` 里改;改完先跑
   `python -m tools.selftest --db data/cases.db`。
2. **单位**表在 `aspen/units.py`;新增单位同样先跑 selftest,再
   `python -m tools.repair_cases --apply` 回算历史工况(不用回 Aspen 重读)。
3. **焓平衡**口径在 `aspen/balance.py`(纯函数);同上两步。

## 跑起来

```bash
python -m app.api            # 起服务,浏览器开 http://127.0.0.1:8000
python -m app.cli --cases    # 命令行看已存工况(不用开网页)
```

前端是原生 HTML/JS/CSS:**改完刷新即生效**;Python 改完**必须重启服务**。

# HTTP 接口参考

工具是一个本地网页应用:Python 标准库 `http.server` 提供接口,前端是原生 HTML/JS。
默认端口 `8000`,数据存在 `data/cases.db`(可用 `python -m app.api --port` 改端口)。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/api/read` | 起一次后台读取;已有任务在跑时返回 409 |
| GET | `/api/read/status` | 读取进度 / 结果(前端每 0.6 s 轮询) |
| GET | `/api/compare?base=1&other=2` | 逐流股 + 逐设备对比两个工况(A / B / 差值 / 状态变化) |
| GET | `/api/cases/{id}/connections` | 设备 ↔ 流股连接关系(方向 / 换热器冷热侧 / 塔进料板 / 来源) |
| GET | `/api/cases/{id}/flowsheet` | 流程图数据(节点 = 设备、边 = 设备间流股、边界进料 / 产品) |
| GET | `/api/cases/{id}/impact` | 报错设备的波及范围(哪些流股的数据不可用 / 仅告警) |
| DELETE | `/api/cases/{id}` | 删除工况及其全部明细(流股 / 设备 / 连接 / 塔剖面 / 组成) |
| GET | `/api/cases/{id}/streams/{流股}/composition` | 单条流股的组成;没存过就现从 Aspen 读一条并缓存,读不了时返回 409 + 原因 |
| GET | `/api/cases/{id}/export.xlsx` | 导出 Excel(给人看) |
| GET | `/api/cases/{id}/export.ai.zip` | 导出 AI 分析包(给 AI 读) |

## 使用注意

- `{流股}` 要用工况里的流股名,特殊字符请做 URL 编码。
- 组成接口在「没缓存 + 当前 Aspen 不是这份工况的来源模型或已重跑」时返回 **409**,
  原因原样写在响应里,前端直接显示。
- 删除是物理删除,工况的明细(流股 / 设备 / 连接 / 塔板 / 组成)一并清掉。
- 导出 Excel 需要 `openpyxl`(见 `requirements.txt`)。

## 相关代码

| 文件 | 职责 |
| --- | --- |
| `app/api/server.py` | 路由、静态文件、导出 |
| `app/api/read_job.py` | 读取后台任务(进度 / 状态机) |
| `app/api/composition.py` | 组成按需读与缓存 |
| `app/api/aipack.py` | AI 分析包生成 |

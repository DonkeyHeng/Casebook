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
| GET | `/api/cases/{id}/pipes` | **管路选型**(实时算、不写库);参数见下;标准表未录入时返回 409 |
| GET | `/api/pipe-tables` | 管路选型依赖的三张标准表录到什么程度(不需要工况) |
| DELETE | `/api/cases/{id}` | 删除工况及其全部明细(流股 / 设备 / 连接 / 塔剖面 / 组成) |
| GET | `/api/cases/{id}/streams/{流股}/composition` | 单条流股的组成;没存过就现从 Aspen 读一条并缓存,读不了时返回 409 + 原因 |
| GET | `/api/cases/{id}/export.xlsx` | 导出 Excel(给人看);标准表录入后多出「管道」「管道计算书」两张表 |
| GET | `/api/cases/{id}/export.ai.zip` | 导出 AI 分析包(给 AI 读);多一份 `pipe_schedule.csv` |

## 管路选型 `/api/cases/{id}/pipes`

```
GET /api/cases/21/pipes?liquid=1.5&gas=15&two=8&pump_inlet=0.8&factor=1.1&floor=1.0&max_dn=600&material=S30408
```

参数全部可省(省了用 `app/design/pipe.py` 的默认值):流速 m/s(液 / 气 / 两相 / 泵入口)、
设计压力系数与下限 MPa、DN 上限、默认牌号。

响应:

| 字段 | 内容 |
| --- | --- |
| `rows` | 逐流股的选型结果 + 计算书(`dn` / `od_mm` / `wall_mm` / `grade_code` / `t_calc_mm` / `warnings` …) |
| `missing` | 没法选型的流股及原因(缺体积流量、超出 DN 表) |
| `summary` | 流股数 / 已选型 / 缺数据 / 有提示 |
| `validation` | V1–V9 校验:`findings[{stream, code, level, message, field}]` + 错误 / 警告计数,`field` 是这条发现该标在哪一列 |
| `options` | 服务端**实际采用**的口径(前端拿它核对有没有被静默换掉) |
| `tables` | 三张标准表的录入状态;`ready=false` 时表能出,但缺的那部分列为空 |

**409** = 标准表没录全(`DN→外径` 或许用应力为空),响应里有 `error` 与 `tables`。
这时改参数不会让结果变好 —— 缺的是标准数据。

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

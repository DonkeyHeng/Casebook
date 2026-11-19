# samples/ — 公开样本数据

一份真实但**失败**的 Aspen 工况快照,给没有 Aspen 或不想先读自己模型的人体验界面、核对判定逻辑用。
它是全仓库**唯一入库存档的 `.db`**(`.gitignore` 中 `*.db` 唯独放行 `samples/*.db`)。

| 文件 | 内容 |
| --- | --- |
| `case-23-error.db` | 一份异常工况快照:121 流股 / 77 模块 / 214 条连接 / 401 条塔板级记录 / 2178 条组成(18 组分 × 121 流股,组分字典带 CAS)/ 7 台泵;判定为 `error`、未收敛。库内也带 `pipe_run` / `pipe_setting` / `pump_selection` 表,可离线试两侧管段与选型台账 |
| `piping-classes.example.json` | 管路选型的**管道等级表示例**(流股名前缀 + 介质代号 → 材质 / 腐蚀裕量)。复制到 `data/config/piping-classes.json` 生效 |
| `sections.example.json` | 衡算报告的**工段(界区)边界示例**(按工况 id:进界流股 / 出界流股 / 设备归堆)。复制到 `data/config/sections.json` 生效;不填则按设备名自动推草稿 |
| `pipe-standards-template/` | 标准表 CSV 的**格式模板**:三份文件各只有表头与 2 行虚构数值,不含任何真实国标数据。用来对照列名填写,导入时会被校验识别并拒绝。详见该目录的 `README.md` |

## 为什么放一份「有错误的数据」

- 判定横幅、波及范围、流程图、塔剖面、流股组成都能在**不连 Aspen** 的情况下看到真实样子
- 它本身就是异常工况,适合用来核对判定逻辑:
  - `CVSTAT=1`(收敛块出错)
  - T0202 报错:`COLUMN DRIES UP OR COLUMN FLOWS VIOLATE BUILT-IN LIMITS`
  - T0205 报错:`COMPONENT BALANCE EQUATIONS FAILED TO CONVERGE.`
  - T0201 / T0206 告警
- 设备页的「泵」页签在这份数据上是满的(7 台泵,扬程 / 流量 / 效率 / 轴功率 / 压升 / 密度都有值),
  可以离线看泵参数卡与选型入口;饱和蒸气压 p_v 读的是进口流股泡点压力,7 台都取到了

## 怎么用

**桌面版最简单**:双击 `Casebook-Desktop.exe` 打开界面,首页提示「还没有工况」时点
**「载入示例工况」** 按钮,示例会装进本地库(原库先备份成 `cases.db.bak`),不用复制任何文件。
命令行方式 `Casebook-Desktop.exe --sample` 效果相同。

**源码版**(示例库就在本目录):

```bat
copy data\cases.db data\cases.local.db
copy samples\case-23-error.db data\cases.db
python -m app.api          # 打开 http://127.0.0.1:8000
```

## 数据说明

- 快照里的运行用户名已替换为 `user`,模型路径只留文件名,不含本机信息;模型内容哈希已清空(示例是可移植快照)
- 模型本体 `.bkp` **不在**仓库里(见 `.gitignore`),这里只有读数
- 数据来自项目作者自己的 Aspen 模型,仅用于演示与自测,**不能作为工程设计依据**

## 已知限制

- **对比页需要两个工况**:这份只有一个。要试对比,得再读一个工况,或换另一份库。
- 塔/反应器在焓平衡里显示「不校验」,这是规则预期(见 `app/aspen/balance.py`)。

许可:与本项目一致,采用 [Apache License 2.0](../LICENSE)。

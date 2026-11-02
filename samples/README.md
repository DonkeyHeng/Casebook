# samples/ — 公开样本数据

一份真实但**失败**的 Aspen 工况快照,给没有 Aspen 的人体验界面、验证判定逻辑用。
它是全仓库**唯一入库存档的 `.db`**(`.gitignore` 里 `*.db` 唯独放行 `samples/*.db`)。

| 文件 | 内容 |
| --- | --- |
| `case-22-error.db` | 工况 #22:121 流股 / 77 模块 / 214 条连接 / 401 条塔板级记录;`severity=error`、未收敛 |

## 为什么公开这份「有错误的数据」

- 判定横幅、波及范围、流程图、塔剖面都能在**不连 Aspen** 的情况下看到真实样子
- 它本身就是**异常工况**,天然是回归样本:
  - `CVSTAT=1`(收敛块出错)
  - `T0202` 报错:`COLUMN DRIES UP OR COLUMN FLOWS VIOLATE BUILT-IN LIMITS`
  - `T0205` 报错:`COMPONENT BALANCE EQUATIONS FAILED TO CONVERGE.`
  - `T0201` / `T0206` 告警
- 来源:项目作者自己的模型(`model-full`,121 流股),读数不含他人数据

## 怎么用

```bash
# 想直接看界面(不需要 Aspen)。先备份原来的库!
cp data/cases.db data/cases.local.db
cp samples/case-22-error.db data/cases.db
python -m app.api          # 打开 http://127.0.0.1:8000
```

## 已知限制

- **没有组成数据**(`stream_components` 为空):「流股组成」抽屉会提示读不到 —— 未连 Aspen 时的预期行为,不是数据坏了。原因见下面「组成为什么不跟着主读取」。
- **对比页需要两个工况**:这份只有一个。要试对比,得再读一个工况,或在库里插第二条 case。
- 塔/反应器在焓平衡里显示「不校验」,这是规则预期(见 `app/aspen/balance.py`)。

### 组成为什么不跟着主读取

18 组分 × 2 相 × 每条流股 = 36 次 COM 调用,是 6 个基础字段的 6 倍:121 条流股从 9 s 涨到 41 s。
所以主读取刻意不读组成,改成「点开哪条读哪条」,读完缓存进该工况的 `stream_components`。

想给这份样本补组成,**不能靠再点一次「读取当前工况」**(那不会读组成),而且这份已经补不回来了:
读取组成有三重校验 —— 当前打开的模型必须就是工况来源模型、运行 ID 必须还是 `_0941waz`
(模型重跑过就拒绝,避免把不同次运行的温度/流量和组成混在一起算)。

要一份带组成的样本,必须**重新读一次**:

```bash
# Aspen 打开 model-full 的模型并跑完,然后:
python -m app.cli --read --composition      # 全量组成,较慢(约 +30 s)
```

得到的是一份新工况,不是这份 #22。

## 脱敏

- `run_user` → `user`
- `model_path` → 相对路径 `model-full/ASPEN 全流程模拟.apw`
- 模型本体 `.apw` **不在**仓库里(见 `.gitignore`),这里只有读数

许可:随仓库 LICENSE 一并授权(仓库 LICENSE 定稿后本条替换为具体条款)。

# samples/ — 公开样本数据

一份真实、**失败但带全部流股组成**的 Aspen 工况快照,给没有 Aspen 的人体验界面、验证判定逻辑用。
它是全仓库**唯一入库存档的 `.db`**(`.gitignore` 里 `*.db` 唯独放行 `samples/*.db`)。

| 文件 | 内容 |
| --- | --- |
| `case-23-error.db` | 工况 #23:121 流股 / 77 模块 / 214 条连接 / 401 条塔板级记录 / **2178 条组成**(18 组分 × 121 流股);`severity=error`、未收敛 |
| `piping-classes.example.json` | 管路选型的**管道等级表示例**(流股名前缀 + 介质代号 → 材质 / 腐蚀裕量)。复制到 `data/config/piping-classes.json` 生效 |

## 为什么公开这份「有错误的数据」

- 判定横幅、波及范围、流程图、塔剖面、**流股组成**都能在**不连 Aspen** 的情况下看到真实样子
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
cp samples/case-23-error.db data/cases.db
python -m app.api          # 打开 http://127.0.0.1:8000
```

## 已知限制

- **对比页需要两个工况**:这份只有一个。要试对比,得再读一个工况,或在库里插第二条 case。
- 塔/反应器在焓平衡里显示「不校验」,这是规则预期(见 `app/aspen/balance.py`)。

## 组成是怎么补进去的

主读取刻意不读组成(36 次 COM/流股,121 条从 9 s 涨到 41 s),所以这份的组成是读完工况后、
趁 Aspen 还开着且模型没重跑,用 `tools/fill_composition.py` 逐条补进库的:

```bash
python tools/fill_composition.py --case 23      # 121 条,约 36 s
```

补数的前提和网页「点开组成」一样:当前打开的必须是这份工况的来源模型、运行 ID 未变
(见 `app/api/composition.py` 的三重校验)。这份样本已经把组成固化进库,别人不需要 Aspen 也能看。

## 脱敏

- `run_user` → `user`
- `model_path` → 相对路径 `model-full/ASPEN 全流程模拟.apw`
- 模型本体 `.apw` **不在**仓库里(见 `.gitignore`),这里只有读数

许可:与本项目一致,采用 [Apache License 2.0](../LICENSE)。

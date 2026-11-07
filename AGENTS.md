# aspen-insight — 协作约定

本文件写给在本仓库工作的开发者与 AI 助手。动手改代码前,先读
[`docs/conventions.md`](docs/conventions.md) 第 5 节的命名与文档检查清单。

## 1. 改代码的硬约束

- 静态文件(HTML / JS / CSS)改完刷新即生效;**Python 改了必须重启服务**
  (`pythonw -m app.api --port 8000`,或双击 `start.cmd`)。
- 动了**判定 / 单位 / 焓平衡**口径,必须先跑回归自测,再回算历史工况:

  ```bash
  python -m tools.selftest --db data/cases.db
  python -m tools.repair_cases --apply
  ```

  判定规则写在 `app/aspen/reader.py` 顶部与 `tools/selftest.py` 开头,改之前先读。
  自测网不需要 Aspen,任何人本地都能跑。
- `app/aspen/` 只读 Aspen,不调用 Run / Reinit / Save,不要引入写模型的代码路径。

## 2. 数字纪律

- 结论里的数字必须来自**实测**,注明样本规模;没验证就写「未验证」。
- 不要把估算当实测写进文档或提交信息。

## 3. 文件命名与文档规范(必须遵守)

新建 / 改名任何文件、目录、文档之前,先过 [`docs/conventions.md`](docs/conventions.md) 第 5 节检查清单。摘要:

- **路径全 ASCII**:目录小写 kebab-case、脚本 snake_case、数据 / 样本小写连字符;中文只进内容,不进路径。
- 放对位置:代码 `app/`、脚本 `tools/`、文档 `docs/`(并进 `docs/README.md` 索引)、样本 `samples/`、标准 `standards/`。
- 根 README 只写给外部使用者看:固定结构(定位、徽章、醒目说明、功能、环境、快速开始、样本、数据、文档、结构、FAQ、限制、贡献、许可、致谢、成本);**不写**内部待办、开发过程与工具环境细节。
- 每个顶层目录都要有 README,写清「放什么、能不能删、怎么用」。
- 提交信息用 `type(scope): 中文摘要`(feat / fix / docs / refactor / chore / style),大改动带正文;不带 `P0` / `P1` 编号。
- 改路径要同步:根 README 目录树、文档引用、脚本默认路径、`.gitignore`、文档互链,最后 grep 确认无残留。

## 4. 数据与安全

- 模型、标准 PDF 原文、本地工况库、日志、截图**一律不入库**(已写入 `.gitignore`)。
- 样本必须脱敏后入库:`run_user` → `user`、`model_path` → 相对路径,不留本地绝对路径与个人信息。
- **不提交他人数据**:他人的模型、他人读数、他人作品的分析结果都不进仓库。
- 提交前自查:
  ```bash
  python -m tools.check_public_safe --all          # 路径 / 密钥 / 隐私 / 内网
  git grep -nE "参考附录|参考附表|参考作品|比赛|参赛|大学|学院|\.bkp 的名字"  # 他人数据
  ```
  `check_public_safe` **只管路径与密钥,扫不出"别人的数据"** —— 文档里别写他人模型的
  文件名、行数统计、流速/壁厚取值、校名或队名。这类内容只留在 `docs/_internal/`(gitignore)。


## 5. 动手前先看别人怎么做过

遇到新功能、新算法、没做过的集成,先调研现成实现再动手:

1. 用 `web_search` 加 `site:github.com` 定位候选仓库。
2. 读它的 README 与核心源码,再决定是用、改造还是自己写。
3. 在 PR / 提交正文里补一句「参考了什么 / 为什么不用」。

# 贡献指南

欢迎 issue 与 pull request。本项目是个人维护的本地工具,合并节奏不一定快,但每条都会看。

动手前请先读:

1. [`AGENTS.md`](AGENTS.md) —— 改代码的硬约束与命名规范(摘要版)
2. [`docs/conventions.md`](docs/conventions.md) —— 文件命名、文档与提交信息的完整规范
3. [`app/README.md`](app/README.md) —— 代码分层

## 环境

```bash
git clone https://github.com/DonkeyHeng/Casebook.git
cd Casebook
python -m pip install -r requirements.txt
```

- Windows 10 / 11,**Aspen Plus V15**(需自备正版许可证)
- Python 3.10+
- 只有实际连 Aspen 读数时才需要 Aspen;跑测试、看界面用样本库即可

## 没有 Aspen 也能跑

仓库自带一份失败工况样本,直接灌进本地库就能看到完整界面:

```bash
cp samples/case-23-error.db data/cases.db
python -m app.api          # 打开 http://127.0.0.1:8000
```

## 提交前必须跑的自测

```bash
python -m tools.selftest --db data/cases.db
```

不需要 Aspen。**改了判定规则 / 单位换算 / 焓平衡口径,必须让这条全绿**,
否则 PR 会被打回 —— 这三处历史上被改错过三次,判定规则写在
`app/aspen/reader.py` 顶部,改之前先读。

## 代码约定

- 静态前端(HTML / JS / CSS)零构建,直接改文件;**Python 改了要重启服务**
- 注释与文档用中文,路径全 ASCII(目录 kebab-case、脚本 snake_case)
- 不引入需要构建的前端工具链(Node / 打包器) —— 这是本项目的刻意取舍

## 提交信息

```
type(scope): 中文摘要
```

`type` 取 `feat` / `fix` / `docs` / `refactor` / `chore` / `style`。
摘要 ≤ 50 字,不带 `P0` / `P1` 编号;大改动在正文写清动机与影响范围。

## 请勿提交

以下内容已在 `.gitignore` 中,但提交前仍建议自查一遍:

- Aspen 模型与运行文件(`.apw` / `.bkp` / `.ads` / `.his` …)
- 标准规范 PDF 原文
- 本地工况库(`*.db`)、日志、截图
- 本机绝对路径(Windows 用户目录、Linux 家目录)与任何个人信息
- **他人的模型、他人读数、他人作品的分析结果**

样本数据必须脱敏后才能入库:`run_user` 写成 `user`,`model_path` 用相对路径。

## PR 检查清单

- [ ] `python -m tools.selftest --db data/cases.db` 全绿
- [ ] `python -m tools.check_public_safe --all` 无高危项(密钥 / 隐私 / 本机路径 / 内网)
- [ ] 没有新增未忽略的模型 / PDF / 数据库 / 日志
- [ ] 没有本机绝对路径与个人信息
- [ ] 改了路径的话,README 目录树、文档互链、脚本默认路径都已同步
- [ ] 提交信息符合 `type(scope): 摘要` 格式

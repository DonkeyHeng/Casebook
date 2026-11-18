# .github/ — 仓库协作说明

只放 GitHub 侧自动识别的协作文件,不放代码、不放数据。

| 文件 | 是什么 | GitHub 什么时候用它 |
| --- | --- | --- |
| `CONTRIBUTING.md` | 贡献指南:环境要求、没有 Aspen 怎么跑、提交前自检、提交信息格式 | 新建 Issue / PR 时页面自动给出入口 |
| `SECURITY.md` | 安全策略:哪些算漏洞、怎么私密上报 | 「Security」页签的 Report a vulnerability |

## 能不能删

不能。删掉后 GitHub 不再在对应页面提示贡献流程与漏洞上报渠道;
本目录没有运行时依赖,应用启动不读这里,删了不影响程序运行。

## 改的时候注意

- 这里的文字对外可见,不写内网地址、本机路径与个人联系方式。
- 改了提交信息格式那一段,要同步 [`AGENTS.md`](../AGENTS.md) 第 3 节与
  [`docs/conventions.md`](../docs/conventions.md) 的提交信息规范。

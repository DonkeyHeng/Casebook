# .github/ — GitHub 协作配置

| 文件 | 用途 |
| --- | --- |
| `workflows/ci.yml` | CI 三道闸门：`ruff check` / `ruff format --check`、离线自测（`tools.selftest` + `pytest`）、前端渲染回归 |
| `CONTRIBUTING.md` | 贡献流程、提交前检查与代码约定 |
| `SECURITY.md` | 安全问题反馈方式与数据边界 |

CI 不需要 Aspen、网络或本机标准表：节点树夹具与 `samples/case-23-error.db` 已覆盖读取链路。
新增或修改工作流时，保持三道闸门与本地命令一致，避免“本地过、CI 挂”。

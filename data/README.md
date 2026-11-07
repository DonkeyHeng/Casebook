# data/ — 本地数据

**除本 README 外全部不入库**(见根目录 `.gitignore`)。换台机器跑,数据要重新读。

第一次运行后,这个目录会自动生成:

| 文件 / 目录 | 是什么 | 能删吗 |
| --- | --- | --- |
| `cases.db` | SQLite 工况库。表:`cases` / `streams` / `stream_components` / `blocks` / `column_stages` / `connections` | ❌ 删了历史工况全没 |
| `config/` | 本机配置,如管路选型的管道等级表 `piping-classes.json` | ⚠️ 删了要重新配置 |
| `preview/std/docx/*.csv` | 管路选型用的三张标准表(**丢了要重新录入**,见 `docs/pipe-standards-index.md`) | ❌ 建议备份 |
| `preview/` 其它内容 | 界面验证截图、标准 PDF 页图与解析文本、附录解析结果、PyInstaller 打包产物 | ✅ 可再生 |
| `probe_topology.txt` | 拓扑 / 焓值探测报告(`tools/probe_topology.bat` 生成) | ✅ 可再生 |
| `window.json` | 窗口大小 / 位置记忆 | ✅ 下次自动重建 |
| `server.log` / `stop.log` | 运行日志 | ✅ 随时删 |

## 备份 / 重置

- **备份**:先停服务,直接复制 `cases.db` 即可(单文件库,没有额外日志文件)。
- **重置**:停服务,删 `cases.db`;下次点「读取当前工况」会自动建库。
- **回算**(改了判定 / 单位 / 焓平衡之后):
  `python -m tools.repair_cases --apply`,不需要回 Aspen 重读。

## 和 `samples/` 的关系

`samples/` 里有一份**脱敏后的公开样本**(异常工况,含全部流股组成),是唯一入库存档的 `.db`。
想在不连 Aspen、不污染本地库的情况下看界面,把 `samples/case-23-error.db` 复制成
`data/cases.db` 即可(先备份原库)。

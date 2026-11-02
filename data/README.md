# data/ — 本地数据

**除本 README 外全部不入库**(见根目录 `.gitignore`)。换台机器跑,数据得重新读。

| 文件 | 是什么 | 能删吗 |
| --- | --- | --- |
| `cases.db` | SQLite 工况库。表:`cases` / `streams` / `stream_components` / `blocks` / `column_stages` / `connections` | ❌ 删了历史工况全没,`selftest` 也没样本 |
| `probe_topology.txt` | 拓扑 / 焓值探测报告(`tools/probe_topology.bat` 生成) | ✅ 可再生 |
| `preview/` | `*.png` 界面验证截图;`std/` 标准 PDF 页图与解析文本;`docx/` 附录解析结果 | ✅ 可再生 |
| `window.json` | 窗口大小 / 位置记忆 | ✅ 下次自动重建 |
| `server.log` / `stop.log` | 运行日志 | ✅ 随时删 |

## 备份 / 重置

- **备份**:先停服务,直接复制 `cases.db` 即可(单文件库,没有额外日志文件)。
- **重置**:停服务,删 `cases.db`;下次点「读取当前工况」自动建库。
- **回算**(改了判定 / 单位 / 焓平衡之后):`python -m tools.repair_cases --apply`,
  不用回 Aspen 重读。

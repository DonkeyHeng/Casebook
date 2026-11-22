# data/ — 本地数据

**除本 README 外全部不入库**(见根目录 `.gitignore`)。换台机器跑,数据要重新读。

第一次运行后,这个目录会自动生成:

| 文件 / 目录 | 是什么 | 能删吗 |
| --- | --- | --- |
| `cases.db` | SQLite 工况库。主表 `cases` / `streams` / `stream_components` / `components` / `component_cas` / `blocks` / `column_stages` / `connections`；另有 `pipe_run`（两侧管段 L / Σζ / 人工 DN，带版本号）、`pipe_setting`（管道页参数）、`pump_selection`（选型台账）、`pump_design`（旧版兼容） | ❌ 删了历史工况全没 |
| `config/` | 本机配置:`sections.json`(工段边界,含真实位号)与 `piping-classes.json`(管道等级表,导入后才有) | ⚠️ 删了要重新配置 |
| `preview/std/docx/*.csv` | 老位置的三张标准表 CSV；没有版本指针时从这里读取（**丢了要重新录入**，见 `docs/pipe-standards-index.md`） | ❌ 建议备份 |
| `preview/std/versions/<时间戳>/` + `preview/std/current.txt` | 新导入走版本化目录：每次导入一整套 CSV，`current.txt` 指向当前生效版本，避免新旧表混用 | ❌ 建议备份 |
| `preview/` 其它内容 | 界面验证截图、标准 PDF 页图与解析文本、附录解析结果 | ✅ 可再生 |
| `preview/build/` | PyInstaller 中间产物（`tools/build_exe.py` 生成） | ✅ 可删，下次打包重建 |
| `preview/dist/` | 发布包：`Casebook-<版本>.zip`、`Casebook-windows-x64.zip`、`Casebook.exe`（`tools/make_release.py` 生成） | ✅ 可删，留最新一版即可，历史版本在 GitHub Releases |
| `webview/` | 桌面壳的 WebView2 缓存（页面 / 着色器缓存、站点数据等本机生成物） | ✅ 可删，下次启动重建 |
| `probe_topology.txt` | 拓扑 / 焓值探测报告(`tools/probe_topology.bat` 生成) | ✅ 可再生 |
| `desktop.log` | 桌面壳启动 / 运行日志；窗口闪退时先看这里 | ✅ 可随时删 |
| `exports/` | 桌面壳内导出的 Excel / Word / zip，导出后自动打开 | ✅ 可随时删 |
| `window.json` | 窗口大小 / 位置记忆 | ✅ 下次自动重建 |
| `server.log` / `stop.log` | 源码模式的运行日志 | ✅ 随时删 |
| `server.pid` | 服务启动时写的进程号(`tools/stop.py` 用它停服务) | ✅ 停服务后即删 |

## 备份 / 重置

- **备份**:先停服务,直接复制 `cases.db` 即可(单文件库,没有额外日志文件)。
- **重置**:停服务,删 `cases.db`;下次点「读取当前工况」会自动建库。
- **腾空间**:`webview/`、`preview/build/`、`preview/dist/`、日志与 `exports/` 都可以直接删,
  不影响 `cases.db` 与 `config/`;`preview/std/` 与 `preview/std/versions/` **不要删**(标准表要重新录入)。
- **回算**(改了判定 / 单位 / 焓平衡之后):
  `python -m tools.repair_cases --apply`,不需要回 Aspen 重读。

## 和 `samples/` 的关系

`samples/` 里有一份**脱敏后的公开样本**(异常工况,含全部流股组成),是唯一入库存档的 `.db`。
想在不连 Aspen、不污染本地库的情况下看界面,把 `samples/case-23-error.db` 复制成
`data/cases.db` 即可(先备份原库)。

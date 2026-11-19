# tools/ — 脚本

不是应用运行时的一部分,是诊断、维护与发布用的辅助脚本。都在**项目根目录**执行。

| 脚本 | 作用 | 需要 Aspen | 会写东西吗 |
| --- | --- | --- | --- |
| `probe_topology.bat` | **双击入口**:只读探测拓扑 / 焓值 / 公用工程,产出 `data/probe_topology.txt` | ✅ 模型已打开且跑过 | 写 `data/probe_topology.txt` |
| `probe_topology.py` | 上面 bat 的本体,支持 `--model` / `--out` | ✅ | 同上 |
| `probe_common.py` | 探针共用的 COM 读取件:附着 Aspen、在结果树里找节点、读子节点与值单位 | ✅ | 只读 |
| `probe_aspen.py` | COM 诊断:注册表、附着、字段全名(`--attach --deep`) | ✅ | 写 `--out` 指定目录 |
| `probe_pump.py` | 泵字段探测:泵块 Output 全节点、进出口流股的蒸气压/密度、Input 里的曲线 | ✅ | 只打印(`--save` 写 `probe_out/`) |
| `probe_pv.py` | 蒸气压 p_v 探测:逐台泵进口流股试候选节点、扫子树、按数值反查,定 p_v 到底在哪个节点 | ✅ 模型已打开且跑过 | 只打印(`--save` 写 `probe_out/`) |
| `probe_components.py` | 组分字典探测:打印 `\Data\Components` 树与各候选父路径命中情况,判断这版 COM 树有没有组分属性层(CAS 读取路径的依据) | ✅ | 只打印 |
| `probe_viscosity.py` | 粘度节点探测:打印流股 `Output\STRM_UPP` 下的子节点(`PL` / `PBUB` / `MUMX`)与候选路径读到的值+单位;加 `--leaves` 列出 5 层内所有有值叶子 | ✅ | 只打印 |
| `probe_blocks.bat` / `probe_blocks.py` | **双击入口**:探测塔的冷凝器/再沸器负荷节点、换热器面积节点(Output 与 Input 都扫) | ✅ 模型已打开且跑过 | 只打印(`--save` 写 `probe_out/`) |
| `dump_fixture.py` | 把当前打开的模型导出成**脱敏节点树夹具**(流股名/设备名自动改写),写 `tools/fixtures/<case>/tree.json` | ✅ 模型已打开且跑过 | 写 `tools/fixtures/` |
| `fake_session.py` | 回放夹具的假 COM session:`read_case()` 不连 Aspen 也能跑;带 `drop_subtree` / `as_leaf` / `patch` 变异,用来守版本差异与单点故障 | ❌ | 只读夹具 |
| `fixtures/` | 脱敏节点树夹具(JSON),给 `selftest.py` 的 `test_reader_fixture()` 用;格式与约定见 `fixtures/README.md` | ❌ | 只读 |
| `build_sample.py` | 从当前打开的模型重读一份工况,生成**脱敏**的公开示例库 `samples/case-23-error.db`;`--from-db data/cases.db --case N` 可改成从已读工况复制(不用 Aspen) | ✅ 模型已打开(用 `--from-db` 时不用) | 写 `samples/` |
| `build_sample.bat` | **双击入口**:同上,必须在桌面会话跑(DSH 沙箱里 ROT 为空) | ✅ | 同上 |
| `bench.py` | 性能基准:COM 调用成本与规模曲线 | ✅ | 只打印 |
| `column_diag.py` | 塔逐板剖面 + 异常标记(只标记,不给修复建议) | ✅ | 只打印 |
| `fill_composition.py` | 给已存工况**批量补**流股组成(网页只能一条条点) | ✅ 且必须是同一运行 | 写库 |
| `balance_report.py` | 批量生成《物料与能量衡算报告》.docx:`python -m tools.balance_report --case 40 [--blocks …] [--families …]`;与网页「衡算报告」页同一套代码 | ❌ | 写 `data/preview/`(或 `--out`) |
| `selftest.py` | 回归自测的**薄入口**,用例正文在 `tests/`(pytest);判定 / 单位 / 焓平衡 / 管路选型 / 泵 / HTTP,拿真实工况当固定样本;含 `test_reader_fixture()` 用 `fixtures/` 回放整棵 COM 节点树 | ❌ | 只读库 |
| `make_pipe_schedule.py` | 命令行生成《管道选型一览表》.docx(与网页导出同一套代码):`python -m tools.make_pipe_schedule --case 55 [--audit] [--out …]` | ❌ | 写 `data/preview/`(或 `--out`) |
| `check_render.mjs` | 前端渲染回归（193 条）+ 模块依赖静态检查（import 可达、无漏 import）：Node + DOM 桩跑真实前端模块的泵悬浮窗 / 两侧管段 / 流股表 / 首页 / 家族页签 / λ 口径 / 对比页 / 报告勾选 / 缺字段兜底，挡住「模板读 state 缺字段 → 整块静默空白」；`node --check` 查不出这类问题 | ❌ | 只打印 |
| `repair_cases.py` | 按最新规则 / 单位表回算历史工况,**不用回 Aspen 重读** | ❌ | `--apply` 时写库 |
| `stop.py` | 按端口停服务:`python tools/stop.py 8000` | ❌ | 停进程 |
| `ui_sweep.py` | 界面回归自检:无头浏览器过 7 个页签(浅色 + 深色),截图并收集 JS 异常 / `console.error` / 失败请求;需服务已启动 + 本机 Chrome/Edge | ❌ | 写 `data/preview/ui-check/`(不入库) |
| `make_icon.py` | 源图 → `.ico`(圆形抠底 + 6 个尺寸) | ❌ | 写 `assets/` |
| `check_public_safe.py` | 提交 / 发布前自检:密钥、隐私、本机路径、内网、误入库;`--all` 连全历史一起查 | ❌ | 只打印 |
| `build_exe.py` | 用 PyInstaller 打包：默认单文件浏览器版，`--desktop --onedir` 出桌面壳目录版（需要 `pip install pyinstaller`） | ❌ | 写 `data/preview/{dist,build}` |
| `make_release.py` | 打源码 zip 与 Windows 桌面 zip 两个发布包，打印大小与 SHA-256 | ❌ | 写 `data/preview/dist` |

## 常见用途

- 「读取当前工况」报「看不到 Aspen」→ `probe_aspen.py`,先看注册表与附着
- 怀疑字段名 / 连接关系读错 → `probe_topology.bat`(双击)
- 要确认「扬程 / 汽蚀余量 / 蒸气压 能不能直接从 Aspen 读」→ `probe_pump.py --attach --save`
- 示例库要跟着新字段更新(读的字段变了、示例还是旧的)→ `build_sample.py`,只读重读一份并脱敏
- 改了判定 / 单位 / 焓平衡口径 → 先跑 `selftest.py`(或 `python -m pytest tests/ -q`);过了再 `repair_cases.py --apply`
- 改了 `reader.py` 里读的节点路径、或换了 Aspen 版本 → 用 `dump_fixture.py` 重采一份
  `fixtures/`,再跑 `selftest.py`:夹具会拦住"新路径拼错 / 版本少了一层"这类静默变空
- 改了 `app/web/static/` 下任意 JS 的渲染逻辑 → `node --check` 查语法,再 `node tools/check_render.mjs` 查静默空白
- 改了前端 / CSS 且服务已起来 → `python -m tools.ui_sweep`(无头浏览器过 7 页 × 两主题,抓控制台报错);脚本不会自己启动服务,截图不入库
- 读完工况、想给整份工况补组成(导出样本 / 离线分析)→ `fill_composition.py --case N`,要趁 Aspen 还开着
- 读得太慢 → `bench.py`
- 提交前 / 发布前 → `check_public_safe.py --all`
- 要发版 → `build_exe.py --desktop --onedir`（先出桌面版）→ `make_release.py`（两个 zip）

## 注意

- `probe_*.py` 都要求**在真实桌面会话**里跑,且与 Aspen 同权限级别。
- 探针的**导入与 `--help` 不需要 Aspen 也不需要 pywin32**:`tests/test_probe_smoke`
  会离线逐个验证;pywin32 缺失只在真探测时报错(退出码 3)。改探针后不用连 Aspen
  也能先过这道冒烟。
- 共用的读取件在 `tools/probe_common.py`;`probe_pump.py` 里的 children / name_of / value_of /
  unit_of 是它的别名,不是另一份实现。
- `repair_cases.py --apply` 会改 `data/cases.db`,跑之前先复制一份库。
- 改了安装步骤 → 同时改 `docs/install.md` 与 `docs/install-text.txt`(发布包根目录里的 `安装说明.txt` 就是后者)。

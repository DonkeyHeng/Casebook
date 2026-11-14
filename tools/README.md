# tools/ — 脚本

不是应用运行时的一部分,是诊断、维护与发布用的辅助脚本。都在**项目根目录**执行。

| 脚本 | 作用 | 需要 Aspen | 会写东西吗 |
| --- | --- | --- | --- |
| `probe_topology.bat` | **双击入口**:只读探测拓扑 / 焓值 / 公用工程,产出 `data/probe_topology.txt` | ✅ 模型已打开且跑过 | 写 `data/probe_topology.txt` |
| `probe_topology.py` | 上面 bat 的本体,支持 `--model` / `--out` | ✅ | 同上 |
| `probe_aspen.py` | COM 诊断:注册表、附着、字段全名(`--attach --deep`) | ✅ | 写 `--out` 指定目录 |
| `probe_pump.py` | 泵字段探测:泵块 Output 全节点、进出口流股的蒸气压/密度、Input 里的曲线 | ✅ | 只打印(`--save` 写 `probe_out/`) |
| `probe_pv.py` | 蒸气压 p_v 探测:逐台泵进口流股试候选节点、扫子树、按数值反查,定 p_v 到底在哪个节点 | ✅ 模型已打开且跑过 | 只打印(`--save` 写 `probe_out/`) |
| `build_sample.py` | 从当前打开的模型重读一份工况,生成**脱敏**的公开示例库 `samples/case-23-error.db` | ✅ 模型已打开 | 写 `samples/` |
| `bench.py` | 性能基准:COM 调用成本与规模曲线 | ✅ | 只打印 |
| `column_diag.py` | 塔逐板剖面 + 异常标记(只标记,不给修复建议) | ✅ | 只打印 |
| `fill_composition.py` | 给已存工况**批量补**流股组成(网页只能一条条点) | ✅ 且必须是同一运行 | 写库 |
| `selftest.py` | 判定 / 单位 / 焓平衡 / 管路选型回归自测,拿真实工况当固定样本 | ❌ | 只读库 |
| `repair_cases.py` | 按最新规则 / 单位表回算历史工况,**不用回 Aspen 重读** | ❌ | `--apply` 时写库 |
| `stop.py` | 按端口停服务:`python tools/stop.py 8000` | ❌ | 停进程 |
| `ui_sweep.py` | 界面回归自检:无头浏览器过 6 个页签(浅色 + 深色),截图并收集控制台报错 | ❌ | 写 `data/preview/ui-check/` |
| `make_icon.py` | 源图 → `.ico`(圆形抠底 + 6 个尺寸) | ❌ | 写 `assets/` |
| `check_public_safe.py` | 提交 / 发布前自检:密钥、隐私、本机路径、内网、误入库;`--all` 连全历史一起查 | ❌ | 只打印 |
| `build_exe.py` | 用 PyInstaller 打单文件 `Casebook.exe`(需要 `pip install pyinstaller`) | ❌ | 写 `data/preview/{dist,build}` |
| `make_release.py` | 打源码 zip 与 Windows zip 两个发布包,打印大小与 SHA-256 | ❌ | 写 `data/preview/dist` |

## 常见用途

- 「读取当前工况」报「看不到 Aspen」→ `probe_aspen.py`,先看注册表与附着
- 怀疑字段名 / 连接关系读错 → `probe_topology.bat`(双击)
- 要确认「扬程 / 汽蚀余量 / 蒸气压 能不能直接从 Aspen 读」→ `probe_pump.py --attach --save`
- 示例库要跟着新字段更新(读的字段变了、示例还是旧的)→ `build_sample.py`,只读重读一份并脱敏
- 改了判定 / 单位 / 焓平衡口径 → 先跑 `selftest.py`;过了再 `repair_cases.py --apply`
- 读完工况、想给整份工况补组成(导出样本 / 离线分析)→ `fill_composition.py --case N`,要趁 Aspen 还开着
- 读得太慢 → `bench.py`
- 提交前 / 发布前 → `check_public_safe.py --all`
- 要发版 → `build_exe.py`(先出 exe)→ `make_release.py`(两个 zip)

## 注意

- `probe_*.py` 都要求**在真实桌面会话**里跑,且与 Aspen 同权限级别。
- `repair_cases.py --apply` 会改 `data/cases.db`,跑之前先复制一份库。
- 改了安装步骤 → 同时改 `docs/install.md` 与 `docs/install-text.txt`(发布包根目录里的 `安装说明.txt` 就是后者)。

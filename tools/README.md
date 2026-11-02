# tools/ — 脚本

不是应用的一部分,是调试 / 排错 / 回算用的。都在**项目根目录**跑。

| 脚本 | 作用 | 需要 Aspen | 会写东西吗 |
| --- | --- | --- | --- |
| `probe_topology.bat` | **双击入口**:只读探测拓扑 / 焓值 / Utilities,产出 `data/probe_topology.txt` | ✅ 模型已打开且跑过 | 写 `data/probe_topology.txt` |
| `probe_topology.py` | 上面 bat 的本体,支持 `--model` / `--out` | ✅ | 同上 |
| `probe_aspen.py` | COM 诊断:注册表、附着、字段全名(`--attach --deep`) | ✅ | 写 `--out` 目录 |
| `bench.py` | 性能基准:COM 调用成本与规模曲线 | ✅ | 只打印 |
| `column_diag.py` | 塔逐板剖面 + 异常标记(只标记,不给修复建议) | ✅ | 只打印 |
| `fill_composition.py` | 给已存工况**批量补**流股组成(网页只能一条条点) | ✅ 且必须是同一运行 | 写库 |
| `repair_cases.py` | 按最新规则 / 单位表回算历史工况,**不用回 Aspen 重读** | ❌ | `--apply` 时**写库** |
| `selftest.py` | 判定 / 焓平衡 / 波及范围回归自测,拿 4 份真实工况(#19–#22)当固定样本 | ❌ | 只读库 |
| `make_icon.py` | 源图 → `.ico`(圆形抠底 + 6 个尺寸),并部署副本到 `%LOCALAPPDATA%` | ❌ | 写 `assets/` 和用户目录 |
| `stop.py` | 按端口停服务:`python tools/stop.py 8000` | ❌ | 停进程 |

## 什么时候跑哪个

- 点「读取当前工况」报「看不到 Aspen」→ `probe_aspen.py`,先看注册表与附着
- 怀疑字段名 / 连接关系读错 → `probe_topology.bat`(双击)
- 读了判定 / 单位 / 焓平衡 → 先 `selftest.py`;不过说明改坏了,过了再 `repair_cases.py --apply`
- 刚读完工况、想给整份工况补组成(导出样本 / 离线分析)→ `fill_composition.py --case N`,要趁 Aspen 还开着
- 读得太慢 → `bench.py`

## 注意

- `probe_*.py` 都要求**在真实桌面会话**里跑;DSH 沙箱内 COM 全部不可用(ROT 为空)。
- `repair_cases.py --apply` 会改 `data/cases.db`,跑之前先复制一份库。

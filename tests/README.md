# tests/ — 离线回归用例

用例用 pytest 组织，但**不依赖 Aspen、网络或本机标准表**：判定、单位、焓平衡、管路选型、泵、HTTP 与库一致性都能在普通 Python 环境里跑。

```bash
python -m pytest tests/ -q                 # 全部
python -m pytest tests/test_pump_line.py -q  # 只跑泵两侧管段
python -m tools.selftest                    # 不装 pytest 时的薄入口（用例正文仍在本目录）
python -m tools.selftest --db samples/case-23-error.db  # 追加库一致性检查
```

2026-11-21 实测：`pytest tests/ -q` 为 73 passed；`tools.selftest` 为 1191 条断言（新增说明书一致性用例）。

## 文件

| 文件 | 内容 |
| --- | --- |
| `_harness.py` | 断言与公共辅助：`check` / `check_eq` / `check_close`、`REPO_ROOT`、前端 JS 拼接；失败直接抛 `AssertionError`，pytest 能定位到行 |
| `_suite.py` | 旧 `tools/selftest.py` 的用例清单：显示名 → 用例函数；pytest 与 selftest 跑的是同一批函数 |
| `test_judge.py` | `reader.judge()` 判定与空模型 |
| `test_aspen.py` | 单位表、COM 节点树夹具回放、组成替换、组分字典 CAS / 中文名 |
| `test_balance.py` | 焓平衡、物料衡算、能量衡算与宽表 |
| `test_report.py` | 衡算报告导出、勾选设备、报错波及范围 |
| `test_pipe.py` | 管路十步选型、V1–V14 校核、标准表导入 / 版本化、管道等级表、Re / λ / Σhf |
| `test_pump.py` | 泵派生量、p_v 读取 / 存储 / 沿用、设备表泵列、分类导出、选泵提示词 |
| `test_pump_line.py` | 泵两侧管段：L / Σζ / ΔZ / 管件 → Σhf、缺项与三态、修正 NPSHₐ，以及 `pipe_run` / `pipe_setting` 的读写与版本冲突 |
| `test_calc_manuals_consistency.py` | 计算说明书与 `app/design` 常量、两册公共阈值的一致性；复用 `tools/check_manual_refs.py` 检查全部交叉引用死链 |
| `test_api.py` | HTTP 路由、请求校验、只读 GET 实跑 |
| `test_store.py` | SQLite 表结构、模型身份、旧库迁移与库一致性 |
| `test_tools.py` | 设备家族、塔剖面诊断、探针离线冒烟、示例库安装 |
| `test_paths.py` | `app.paths` 的只读资源 / 可写数据目录解析 |
| `test_suite.py` | `_suite.py` 清单与 pytest 收集结果一致，防止漏登记 |

## 约定

- 新增用例直接写 `test_*.py`；需要同时进 `tools.selftest` 的，再登记到 `_suite.py`。
- 涉及判定 / 单位 / 焓平衡 / 选型口径的改动，先跑本目录，再回算历史工况。
- 夹具在 `tools/fixtures/`，样本库在 `samples/`；用例只读，不往仓库写数据。
- `tests/` 是包（有 `__init__.py`），从仓库根执行，避免相对导入失效。

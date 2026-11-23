"""app/design/heat —— 换热器选型 L1/L2 的纯计算层。

与 `app/design/pipe/` 同一套分工:

* 只吃 Python 数值与字典,不碰 Aspen、不读 `data/`、不写库;
* 内置的污垢 / 经验 U / 公用工程预设字典写死在 `data.py`,冷启动与离线
  单测零配置就能跑;用户覆盖由 `app/store` 读好再传进来(传进来的是
  `(dict, source, warnings)`,本层只认 dict);
* 空值一律 `None` 表示"没读到",不补 0,遵守项目数字纪律。

Phase 1 范围:工艺条件整理、LMTD、反算 U、设计温压、允许压降、污垢、
GB/T 151 编码、EDR 输入清单。Phase 2 才写几何枚举与水力校核。
"""

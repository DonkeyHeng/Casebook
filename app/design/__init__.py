# -*- coding: utf-8 -*-
"""app/design —— 管路选型(纯计算,不碰 Aspen)。

与 `app/aspen/` 的分工:`app/aspen/` 只负责从 Aspen 读数;本包只接收
"流股数值 + 配置",算出 DN / 壁厚 / 管道等级号,可单独跑、也方便写自测。
"""

# 换热器标准系列表 · 格式模板

这个目录只有**表头与 2 行虚构示例**,用来核对列名与写法。示例里的
`area_m2 = 999` 是**故意写错的**,原样导入会被拒绝(导入时会按文本指纹
识别模板)。

## 为什么仓库不带标准数值

`GB/T 28712.2-2023` 的数值表受著作权保护,不能随开源项目分发,Release 里也不放。
请从合法渠道取得标准,自行整理 CSV;页面「标准表 → 下载模板」里拿到的就是这两个文件。

## 两个文件

| 文件 | 来源 | 必需列 |
| --- | --- | --- |
| `hx-28712-2.csv` | GB/T 28712.2-2023 表 5(φ19)/ 表 7(φ25) | DN、shell_id_mm、tube_od_mm、tube_wall_mm、tube_length_mm、tube_pitch_mm、tube_passes、tube_count、area_m2、arrangement |
| `hx-pass-combinations.csv`(可选) | 方案内置 4 行 | shell_passes、tube_passes、need_f_correction、allowed |

可空列:`shell_od_mm`、`pn_bar`、`standard`、`page`、`note`(程数组合表只有 `note`)。

## 填数时的四条易错点

1. **长度是 mm,面积是 m²,公称压力是 bar**。标准原文的 PN 是 MPa:0.25 MPa → 2.5 bar,
   6.40 MPa → 64 bar。不要把 MPa 直接抄进 `pn_bar`。
2. `area_m2` 抄标准表「计算换热面积(A₁)」,不要自己算 π·d·L·n。标准按
   A₁ = n·π·d_o·(L − 2δ − 2L1) 计算(δ 管板厚,L1 伸出长度),与 πdLn 差 1%~2%。
3. `arrangement` 只认 `triangular` / `rotated_triangular` / `square` / `rotated_square`;
   `tube_passes` 只认 1 / 2 / 4 / 6。
4. `standard` 写实际版本(`GB/T 28712.2-2023`)。2023 版与 2012 版数值不同,不能混用。

## 导入后什么时候算「已加载」

- 有效行 ≥ 20、DN ≥ 3 档、管外径 ≥ 2 种 → 状态 `ok`,L2 做标准行枚举;
- 行数或档数不够 → `missing`,L2 降级为「只给 A_req + 经验推荐范围」(`std_skipped=true`);
- 表头缺列或一行也读不出 → `invalid`,同样降级。

L1(工艺条件、LMTD、U 反算、设计温压、编码)**无论如何都不受影响**。

## 导入方式

页面「换热」→ 右侧「标准表」按钮 → 选 CSV → 导入。导入会在
`data/preview/hx-std/versions/<时间戳>/` 下拼一份完整目录,校验通过才切
`data/preview/hx-std/active.txt` 指针;失败不覆盖当前版本。

也可以手工把两个 CSV 放进 `data/preview/hx-std/docx/` 后重启服务。

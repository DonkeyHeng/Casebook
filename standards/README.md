# standards/ — 标准规范原文(不入库)

这里用于放本地查用的标准 PDF。`.gitignore` 已排除本目录下的文件,所以克隆仓库后这里只有本 README。

**为什么仓库里没有标准原文**:国标表格受著作权保护,不能随项目分发。请自行从合法渠道获取。
管路选型实际用到的三张表(GB/T 17395 表 1、表 4,GB/T 20801.1 表 B.1)需要整理成 CSV 后
通过页面「标准表」导入,格式要求见 [`docs/pipe-standards-index.md`](../docs/pipe-standards-index.md)。

| 文件 | 标准 | 管路选型里用来干什么 |
| --- | --- | --- |
| `GBT-17395-2024.pdf` | GB/T 17395-2024 无缝钢管尺寸、外形、重量及允许偏差 | 公称外径 × 壁厚标准组合(表 1 / 2 / 4 / 8 / 9) |
| `GBT-20801.1-2025.pdf` | GB/T 20801.1-2025 压力管道规范 工业管道 | 管道级别(GC1 / GC2 / GC3)与许用应力表 B.1 |
| `GBT-20801.1-2025-表b1.pdf` | 同上,只裁出表 B.1 的 41 页 | 全表录入;配套提示词见 [`docs/pipe-standards-extract-prompt.md`](../docs/pipe-standards-extract-prompt.md) |
| `GBT-20801.1-2025-表b1 (1).pdf/` | 表 B.1 的逐页渲染图(41 张 PNG) | 给能看图的 AI 抄数用,配合上面的提示词 |
| `GBT-8163-2018.pdf` | GB/T 8163-2018 输送流体用无缝钢管 | 牌号(10 / 20 / Q345B 等)与技术要求 |
| `SH-T-3035-2018.pdf` | SH/T 3035-2018 石油化工管道器材选用 | 选型参考(纯扫描件,无文字层) |
| `HG-T-20519.6-管道等级号规则.txt` | HG/T 20519.6 等级号规则原文 | 规则已摘进 [`docs/pipe-class-codes.md`](../docs/pipe-class-codes.md) |

## 注意

- 这些 PDF 的**表格数字常有乱码**(嵌入字体缺 ToUnicode),不能直接复制数字;
  要么渲染成页图人工核对,要么按 `docs/pipe-standards-extract-prompt.md` 的提示词重新录入。
- 表 B.1 整理成 CSV 后,材质代号(A–H)另按 `docs/pipe-material-code-prompt.md` 判定;
  两份提示词都不含真实标准数值,可以随仓库分发。
- 文件名保持 `GBT-<号>-<年>.pdf` / `SH-T-<号>-<年>.pdf` 的形式,方便对照上表。

## 能不能删

能，程序启动与运行都不读这里。删掉只是查表时要重新准备一份原文；
已经导入的标准表 CSV 存在 `data/preview/std/versions/<时间戳>/`（`current.txt` 指向当前生效版本，
老位置 `data/preview/std/docx/*.csv` 仍作兜底），与这些 PDF 无关（见 [`data/README.md`](../data/README.md)）。

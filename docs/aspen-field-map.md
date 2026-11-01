# Aspen Plus V15 真实字段地图(实测)

> 实测时间:2026-10-31 19:40–19:55 (Asia/Shanghai)
> 环境:Aspen Plus **V15**(COM 版本号 `41.0`)/ Python 3.15.2 + pywin32
> 模型:`model\ASPEN 全流程模拟.apw`(32 流股 / 19 模块,已收敛)
> 原则:**本文所有路径都是 `Tree.FindNode()` 跑出来的,不是查文档猜的。**

---

## 0. 一句话结论

COM 路线成立。附着、流股表、设备表、收敛状态、塔板级剖面**全部能读**,单位也能直接读到,
不需要靠命名约定猜字段。

---

## 1. 连接:必须走「文件 moniker」,不能用 ProgID

这是本次最大的坑,也是之前 `--attach` 一直失败的原因。

| 方式 | 结果 |
| --- | --- |
| `GetActiveObject("Apwn.Document")` | ❌ `com_error -2147221021`(`MK_E_UNAVAILABLE`) |
| `GetObject(r"D:\...\model.apw")` | ✅ 成功,拿到完整文档对象 |

原因:Aspen Plus **手工启动**时,只把文档注册成文件 moniker(`...\model.apw`),
不注册 `Apwn.Document` 这个 ProgID。ROT 里实测只有 3 个对象:

```
!{50E9AB23-...}
<repo-root>\ASPEN 全流程模拟.apw        <-- 就是这个(当时模型还在根目录)
<repo-root>\ASPEN 全流程模拟.apw!\
```

> 上表是 2026-10-31 的原始记录。模型已移到 `model/` 子目录,moniker 会变成
> `...\aspen-insight\model\ASPEN 全流程模拟.apw` —— 路径**跟着你打开的那个文件走**,
> 所以代码里不要写死路径,应该扫 ROT 拿。

正确写法:

```python
import win32com.client
doc = win32com.client.GetObject(r"D:\path\to\model.apw")
tree = doc.Tree
```

### 附带发现:DSH 沙箱里读不到 ROT

DSH 的 shell 跑在隔离环境里时,ROT 是**空的**(连 `Shell.Application` 都报同样的
`MK_E_UNAVAILABLE`),但窗口能枚举、`OpenProcess` 也能开。所以:

> **所有 COM 操作必须在真实桌面会话执行**;在 DSH 沙箱内跑会得到"Aspen 没开"的假象。
> 本次是用 `danger-full-access` 才跑通的。

---

## 2. 文档对象(doc)接口

`doc` 实现了 78 个方法,常用的:

| 成员 | 说明 | 实测 |
| --- | --- | --- |
| `doc.Tree` | 数据树根 | ✅ |
| `doc.Name` | `"Aspen Plus 41.0 OLE 服务"` | ✅(不是模型名) |
| `doc.FullName` | **服务器 exe 路径**,不是 apw 路径 | ✅ 反直觉 |
| `doc.ProcessId` | Aspen 进程号(43384) | ✅ |
| `doc.Run2()` | 跑仿真 | 未调用(只读) |
| `doc.Engine.IsRunning` | 是否在算 | ✅ `False` |
| `doc.Engine.ControlPanel` | 控制面板 | ❌ 附加态下报错 |
| `doc.Version / FileName / DisplayName` | —— | ❌ 不存在 |

> 模型文件路径只能从「你用来附着的 moniker 字符串」拿到,`doc` 上读不到。

---

## 3. 树结构

`\Data` 下有 25 个子节点,第一期只用得到 4 个:

```
\Data\Setup
\Data\Components          <- 组分定义
\Data\Streams             <- 流股(32 条)
\Data\Blocks              <- 单元模块(19 个)
\Data\Utilities           <- 公用工程(AIR/ELE/HS/LS/LWAT/MS/WAT)
\Data\Results Summary     <- 运行状态汇总
\Data\Convergence         <- 收敛设置
```

`FindNode()` 找不到时**返回 `None`**,不抛异常 —— 所以"字段不存在"和"值为 None"
在代码里要靠"节点是否存在"来区分,不能只看值。

---

## 4. 流股(Streams)

### 4.1 结果字段

路径:`\Data\Streams\<流股名>\Output\<字段>`

| 语义 | 字段名 | 实测值(2TO4) | 单位 |
| --- | --- | --- | --- |
| 温度 | `RES_TEMP` | 171.196863 | `C` |
| 压力 | `RES_PRES` | 1.0 | `bar` |
| 质量流量 | `RES_MASSFLOW` | 24888.9523 | `kg/hr` |
| 摩尔流量 | `RES_MOLEFLOW` | 176.622126 | `kmol/hr` |
| 汽化率 | `RES_VFRAC` | 0.0877111 | (无量纲) |
| 体积流量 | `RES_VOLFLOW` | 10002.133 | `l/min` |

> **注意**:`TEMP_OUT`、`TEMP_OUT`、`PRES_OUT`、`VFRAC_OUT`、`MASSFLOW`、`MOLEFLOW`
> 这些在 Output 下确实存在,但值是 **`1` 这样的标志位**(表示"要不要输出"),
> **不是物性值**。真正的结果一律是 `RES_` 前缀。这是最容易踩的坑。

### 4.2 组成

| 语义 | 路径 | 说明 |
| --- | --- | --- |
| 液相摩尔分数 | `\Data\Streams\<流>\Output\X\<组分>` | 18 个组分,合计 ≈ 1 |
| 气相摩尔分数 | `\Data\Streams\<流>\Output\Y\<组分>` | 18 个组分,合计 ≈ 1 |
| 组分质量流量 | `\Data\Streams\<流>\Output\MASSFLOW3\<组分>` | kg/hr |

组分名就是用户在 Aspen 里定义的 ID:`H`、`BEN`、`HJX`、`HJW`、`CSHJZ`、`HJC`、
`ETOH`、`HJT`、`HAC`、`WATER`、`HJEX13`、`HJEX14`、`JB`、`O`、`N`、`N:N-D-01`、`DMSO`、`KQ`。

> `MASSFRAC` / `MOLEFRAC` 这两个节点下只有 `MIXED` 一个子节点且值为 1,
> 同样是标志位,不要当组成用。

### 4.3 输入字段

`\Data\Streams\<流>\Input\` 下:`TEMP`(若温度被指定)、`PRES`、`FLOW`、
`BASIS`(摩尔/质量基准)、`VFRAC` 等。示例:`PRES = 1.0 bar`、`FLOW = 0.485431 kmol/hr`。

---

## 5. 单元模块(Blocks)

### 5.1 读类型:用 `AttributeValue(6)`

```python
node = tree.FindNode(r"\Data\Blocks\E0401")
node.AttributeValue(6)      # -> 'Heater'
```

实测同一批节点:

| 调用 | 结果 | 评价 |
| --- | --- | --- |
| `node.Value` | `'HEATER'`、`'ICON1'`、`'FRACT1'` | 半可读,`ICON1` 这种没法用 |
| `node.AttributeValue(6)` | `'Heater'`、`'Pump'`、`'RadFrac'`、`'RStoic'`、`'Flash2'`、`'Mixer'`、`'HeatX'` | ✅ 直接用这个 |

数值属性编号(实测):`0`=内部模型名、`6`=**可读类型名**、`12`=类 ID、`24`=名称。
`HasAttribute(n)` / `AttributeType(n)` 可枚举。

> 已知瑕疵:`T0403` 实际是吸收塔(model `ABSBR1`),`AttributeValue(6)` 仍报 `RadFrac`。
> 对"设备结果表"无影响,但做图标/分类时要注意。

### 5.2 各类设备的结果字段

| 设备类型 | 字段 | 路径 | 单位 |
| --- | --- | --- | --- |
| Heater / Flash2 / RStoic | `QCALC` / `QNET` | `\Data\Blocks\<设备>\Output\` | `cal/sec` |
| HeatX(换热器) | `HX_DUTY` | 同上 | `cal/sec` |
| HeatX | `HX_AREAC` | 同上 | `sqm` |
| Pump | `BRAKE_POWER` / `ELEC_POWER` | 同上 | `kW` |
| Pump | `CEFF` | 同上 | 无量纲 |
| RadFrac / Absorber | `NSTAGE` | `\Data\Blocks\<塔>\Input\NSTAGE` | 个 |
| RadFrac | `B_TEMP\<级>` / `B_PRES\<级>` / `DUTY\<级>` | `\Data\Blocks\<塔>\Output\` | `C` / `bar` / `cal/sec` |

**单位不统一是硬约束**:同一个"热负荷"概念,加热器给 `cal/sec`,泵给 `kW`。
适配层必须读 `UnitString` 做换算,不能假设单位集固定(1 cal/sec = 4.184 W)。

### 5.3 不要用的字段

`DUTY_OUT`、`PRES_OUT`、`DUTY_OUT2` 这些是 0/1 开关,不是数值。
`Output` 下 121~971 个子节点里大部分是这类开关和 `CMF_*`/`CA_*` 计算选项。

---

## 6. 收敛状态

分三层看,缺一层就会误判:

| 层 | 路径 | 用途 |
| --- | --- | --- |
| 全局状态码 | `\Data\Results Summary\Run-Status\Output\RSTAT / CVSTAT / UOSSTAT / PER_ERROR …` | 快速判断"这次跑得干不干净" |
| 模块级状态 | `\Data\Blocks\<设备>\Output\BLKSTAT` + `BLKMSG` | **真正定位问题在哪** |
| 撕裂流误差 | `\Data\Results Summary\Conv-Sum\TEAR-SUMMARY\Output\` | 判断闭环是否数值收敛 |

### 6.1 全局状态码(两次实测对照)

| 字段 | 收敛且无报错 | 有模块报错 | 含义 |
| --- | --- | --- | --- |
| `RSTAT` | 0 | 0 | 运行状态(两次都是 0,不敏感) |
| `CVSTAT` | 0 | **1** | 收敛块状态 |
| `UOSSTAT` | 0 | **1** | 单元操作状态 |
| `PER_ERROR` | 0 | **1** | 与报错同步变化 |
| `CSSTAT` | 2 | 2 | **恒为 2,不要当作失败** |
| `PPSTAT` | 2 | 2 | 恒为 2 |
| `SENSSTAT` | 2 | 2 | 恒为 2 |
| `ITSTAT` | 4 | 4 | 迭代次数(两次都是 4) |

> 结论:`RSTAT` 和 `ITSTAT` 在本机两次样本里都不变,不能单独用来判收敛;
> **`CVSTAT` / `UOSSTAT` / `PER_ERROR` 才会随错误变化**。

### 6.2 模块级状态(最有价值)

```
\Data\Blocks\<设备>\Output\BLKSTAT   -> 0=正常, 1=错误, 2=告警
\Data\Blocks\<设备>\Output\BLKMSG    -> 错误原文(英文)
```

实测样本(model-full,121 流股 / 77 模块):

| 模块 | 类型 | BLKSTAT | BLKMSG |
| --- | --- | --- | --- |
| T0201 | RadFrac | 2 | `PROBLEM(S) ENCOUNTERED DURING PHASE STABILITY CHECK` |
| T0202 | RadFrac | 1 | `COLUMN DRIES UP OR COLUMN FLOWS VIOLATE BUILT-IN LIMITS` |
| T0205 | RadFrac | 1 | `COMPONENT BALANCE EQUATIONS FAILED TO CONVERGE.` |
| T0206 | RadFrac | 2 | `PROBLEM(S) ENCOUNTERED DURING PHASE STABILITY CHECK` |

### 6.3 重要:Run-Status 里**没有** ERRTXT 节点

Aspen 的 `.sum` 文件里有 `DSET RUN-STATUS ERRTXT ...`,但在数据树里
`Run-Status\Output` 的 38 个子节点中**不存在 ERRTXT**。

那段文字等价信息只能从两处拼出来:

1. `model-full/_<runid>.sum` 文件(明文,包含
   "The following Unit Operation blocks were completed with errors: T0202 T0205、
   The following Convergence blocks were completed with errors: $OLVER02");
2. 各模块的 `BLKMSG`(逐条错误原因,更适合做界面)。

> 所以**不要指望从树里读到一句完整的中文结论**,必须逐模块读 `BLKSTAT/BLKMSG` 再汇总。

### 6.4 撕裂流误差

`\Data\Results Summary\Conv-Sum\TEAR-SUMMARY\Output\` 是**按编号展开的数组**:

```
STREAMID\1 = '0404'
VARID\1    = 'H MOLEFLOW'
ERR\1      = 0.0
ERR2\1     = 0.0
CVSTAT\1   = '#'
```

model-full 有 6 条撕裂流,误差分别为 `1.1e-14 / 0.0 / 1.6e-08 / 2.8e-06 / 0.0 / 3.0e-13`。

> ⚠️ **关键认知:撕裂流误差小 ≠ 流程可用。**
> model-full 的撕裂流全部在 1e-14~1e-6 量级(数值上已经收敛),
> 但 T0202/T0205 依然报了实质性错误(塔干涸、组分平衡方程不收敛)。
> 判定必须"撕裂流 + 模块状态"两条腿走路。

---

## 7. 塔板 / 级剖面(二期已确认可行)

路径:`\Data\Blocks\<塔>\Output\<变量>\<级号>`,级号从 `1` 开始。

| 变量 | 含义 | 单位 | 是否有二级子节点 |
| --- | --- | --- | --- |
| `B_TEMP` | 级温度 | `C` | 否 |
| `B_PRES` | 级压力 | `bar` | 否 |
| `DUTY` | 级热负荷 | `cal/sec` | 否 |
| `LIQ_FLOW` / `VAP_FLOW` | 液相/气相流量 | 模型单位 | 否 |
| `RHO_LIQ` / `RHO_GAS` | 密度 | 模型单位 | 否 |
| `MW_LIQ` / `MW_GAS` | 分子量 | 模型单位 | 否 |
| `EFFICIENCY` | 级效率 | 无量纲 | 否 |
| `X` / `Y` | 级液相/气相摩尔分数 | 无量纲 | **是**:`X\<级>\<组分>` |
| `B_K` | 相平衡 K 值 | 无量纲 | **是**:`B_K\<级>\<组分>` |
| `MASS_CONC` | 质量浓度 | 模型单位 | **是**:`MASS_CONC\<级>\<组分>` |

实测:T0401 17 级、T0402 20 级、T0403(吸收塔)4 级。
例:`\Data\Blocks\T0401\Output\B_TEMP\1 = 88.0523 C`。

> 注意层级:标量变量是 `变量\级`,组成类变量是 `变量\级\组分`。
> 遍历时先看该级节点有没有子节点,再决定怎么取值。

---

## 8. 单位处理

`UnitString` 在**不同节点上是两种形态**:有的是方法、有的是属性。统一写法:

```python
u = node.UnitString
unit = u() if callable(u) else u
```

本模型单位集 `METCBAR`,实测出现:`C`、`bar`、`kg/hr`、`kmol/hr`、`cal/sec`、`l/min`、`sqm`、`kW`。

目标单位:`C / bar / kg·h⁻¹ / kW`,换算表:

| 源 | 目标 | 系数 |
| --- | --- | --- |
| `cal/sec` | `kW` | × 0.004184 |
| `kW` | `kW` | 1 |
| `kg/hr` | `kg/hr` | 1 |
| `l/min` | `m³/h` | × 0.06 |
| `sqm` | `m²` | 1 |

> 120~970 个子节点里大部分字段没有单位(空字符串),换算前要先判空。

---

## 9. 性能与规模(实测)

在 model-full(121 流股 / 77 设备)上用 `python -m tools.bench` 实测:

| 阶段 | 耗时 | COM 调用数 |
| --- | --- | --- |
| 附着 `attach()` | 31 ms | — |
| 收敛状态 | 313 ms | 40 |
| 流股(6 字段/条) | 4.6 s | 727 |
| 设备(含类型/负荷/剖面首末级) | 4.8 s | 830 |
| 单条流股组成(18 组分 × 2 相) | 264 ms | 36 |
| **全量(不含组成)** | **9.2 s** | 1598 |
| **全量(含组成)** | **41.1 s** | ~6000 |

**单次 `FindNode` 平均 6.0 ms** —— 这是跨进程 COM 的固有成本,优化空间有限,
所以要靠"减少调用次数"而不是"优化单次调用"。

按线性外推:

| 规模 | 不含组成 | 含组成 |
| --- | --- | --- |
| 200 流股 | ≈ 15 s | ≈ 68 s |
| 500 流股 | ≈ 38 s | ≈ 170 s |
| 1000 流股 | ≈ 76 s | ≈ 340 s |

结论:

- **组成默认不读**。18 组分 × 2 相 × 每条流股 = 36 次调用,是基础字段(6 次)的 6 倍,
  全量读会让耗时从 9 s 涨到 41 s。第二期做"组成分析"时再按需单条读取。
- 全量读取必须放**后台线程 + 进度提示**(9 s 已接近用户耐心上限,1000 流股要 76 s)。
- 表格懒加载:先读名字和 6 个基础字段就够撑起"流股总表",塔板剖面等细节按行展开再读。

---

## 10. 坑清单(按踩坑概率排序)

1. **用 ProgID 附着** → 必失败。必须 `GetObject(apw 路径)`。
2. **在 DSH 沙箱里跑** → ROT 为空,误判成"Aspen 没开"。必须在真实桌面会话跑。
3. **`RES_` 前缀**:`TEMP`/`MASSFLOW` 是开关,真值在 `RES_TEMP`/`RES_MASSFLOW`。
4. **数组节点的 `Value`** 恒为 `1`(它是"容器"),要往下取 `\1`、`\2`。
5. **单位不统一**(`cal/sec` vs `kW`),必须按 `UnitString` 换算。
6. **`FindNode` 返回 `None` 而非异常**,判断字段存在与否要看节点,不看值。
7. **`doc.FullName` 是 exe 路径**,不是模型路径;模型路径得从附着用的 moniker 拿。
8. **`AttributeValue` 只接受数字属性号**,传字符串会 `ValueError`。
9. **`UnitString` 有时是方法有时是属性**。
10. **`\Data\Results Summary\Stream-Sum`** 下有几个节点 `Name` 会抛 `AttributeError`,遍历要有兜底。

---

## 11. 还没确认的东西

| 项 | 状态 | 怎么确认 |
| --- | --- | --- |
| 纯"不收敛"(无模块报错)时的状态码 | ⚠️ 部分 | 已拿到"有模块报错"样本(CVSTAT/UOSSTAT/PER_ERROR=1);还缺"只有撕裂流不收敛"的样本 |
| 能量流(热流)的字段形态 | ❓ 两个模型全是物流 | 找一个含蒸汽/热流的模型 |
| `X1` / `X2` 与 `X` 的区别 | ❓ 疑似子物流(substream) | 多 substream 模型验证 |
| 200–1000 流股的耗时 | ⚠️ 部分 | 已测 121 流股 / 77 模块 ≈ 9 s;按此线性外推 1000 流股 ≈ 75 s |
| 只读入口 `Apwn.ReadOnlyComposite.41.0` | ❓ 未测 | 若存在,可用于"绝不改模型"的强约束 |
| `PER_ERROR` 的确切定义 | ❓ | 两次样本 0 / 1,疑似"出错项计数",未证 |

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

> **没 Run 过的模型长什么样**(2026-11-01 实测):`\Data\Streams\<流股>` 的**名字都在**,
> 但 `Output\RES_*` 节点**不存在** —— 630 个流股字段全为空、UnitString 全空字符串;
> `Run-Status` 的 8 个状态码全是 `None`;`Blocks` 里的 `NSTAGE` 这类**输入值仍可读**
> (74 个设备里有 10 个还能读到东西)。
> 也就是说"有名字、有输入、没结果" —— 判定时不能把"没有任何信号"当成"没问题"。

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

### 6.1 全局状态码(实测对照,2026-11-01 新增两个样本模型样本)

| 字段 | 收敛且无报错 | 全是告警 | 有模块报错 | 含义 |
| --- | --- | --- | --- | --- |
| `RSTAT` | 0 | 0 | 0 | 运行状态(**一直是 0,不敏感**) |
| `CVSTAT` | 0 | 0 | **1** | 收敛块状态。**非 0 才是真出错** |
| `UOSSTAT` | 0 | **1** | **1** | 单元操作状态。1 = 有模块状态非零(**含告警**) |
| `PER_ERROR` | 0 | **1** | **1** | **无区分度,见下** |
| `CSSTAT` | 2 | 2 | 2 | **恒为 2,不要当作失败** |
| `PPSTAT` | 2 | 2 | 2 | 恒为 2 |
| `SENSSTAT` | 2 | 0 / 2 | 2 | 不代表失败 |
| `ITSTAT` | 4 | 4 | 4 | 迭代次数 |

> ⚠️ **2026-11-01 修正:不能按"非 0 就是出错"判。**
>
> 读入一个样本模型(98 流股 / 67 设备)时,`CVSTAT=0`、`UOSSTAT=0`、
> **67 个模块的 `BLKSTAT` 全是 0**,但 `PER_ERROR` 依然是 1。
> 而早先那个真出错的模型(T0202/T0205 是 `BLKSTAT=1`)`PER_ERROR` 也是 1 ——
> **同一取值,两种完全不同的实际情况**。
>
> 另一个样本模型(105 流股 / 74 设备)7 个模块全是 `BLKSTAT=2`(相稳定性告警)、
> 没有一个 `BLKSTAT=1`,但 `UOSSTAT=1`、`PER_ERROR=1` —— 也不该判"错误"。
>
> 结论:**判定只能靠 `CVSTAT` + 模块级 `BLKSTAT`**;
> `UOSSTAT` / `PER_ERROR` / `RSTAT` 只记录、不参与判定。

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

> **控制面板本身读不到**(2026-11-01 复核):`doc.Engine.ControlPanel` 在附加态报错,
> 模型目录里的 `_<runid>.sum` 只有收敛摘要(`TEAR-SUMMARY` + `COMPSTAT`),
> `.sor` / `.sin` 里也搜不到 "completed with warnings" 那句话。
>
> **但不需要它**:控制面板里那句
> `The following Unit Operation blocks were completed with warnings: M0302 E0204 R0301`
> 的模块清单,和逐模块 `BLKSTAT=2` 的集合**逐字相同**(实测同一模型,顺序都一致)。
> 也就是说 `BLKSTAT/BLKMSG` 就是控制面板的结构化版本 ——
> **判"能不能用",只信这两样;别拿 `UOSSTAT`/`PER_ERROR`/撕裂流数值去推翻它**。

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

> ⚠️ **2026-11-01 补充:反过来也不成立 —— `ERR > ERR_TOL` 不能判"没收敛"。**
>

> `TEAR-SUMMARY` 里 **T3 的 `INFO-VAR` 误差 1.656 > 容差 0.0393(42 倍)**,
> 但 Aspen 控制面板只写
> `The following Unit Operation blocks were completed with warnings: M0302 E0204 R0301`,
> `CVSTAT=0` —— **Aspen 自己不认为这条回路没收敛**。
>
> **为什么不能比**:同一模型留下的 `_<runid>.sum` 里,`CNV_TEAR` 每条记录是
> `流股号 ERR 数值A 数值B 变量名 "#" 收敛块` 三段数字,而这台机器上
> **数值B 恒等于 数值A × 10⁴**(逐条核对:1.84e-05/1.84e-01、6.42e-05/6.42e-01、
> 3.93e-06/3.93e-02……)。也就是说你以为的"容差"只是另一个残差的固定缩放,
> **不是独立的收敛判据**,拿 `ERR` 去比它等于比了个寂寞。
>
> 结论:**撕裂流的数字只作为提示列出来,不能替 Aspen 下结论**。
> 判"能不能用"仍然只看模块级 `BLKSTAT` 和 `CVSTAT`。

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

### 8.1 ⚠️ Aspen 的 cal 是 IT 卡(4.1868 J),不是 4.184

2026-11-01 实测(同一模型,同一批结果,只改单位集):

| 单位集 | 原始值 | 换算后 |
| --- | --- | --- |
| MET(`Gcal/hr`) | 28.0 | ? |
| ENG(`Btu/hr`) | 111,112,980 | 32,564.0 kW |

按 `Btu/hr`(1055.05585262 J)换算是 32564.0 kW ⇒ **1 Gcal/hr = 1163.0 kW**
⇒ **1 cal = 4.1868 J(IT 卡)**。若按热化学卡 4.184 算,所有热负荷会**偏低 0.067%**
(27 个设备全部同比例偏,不是随机误差 —— 很容易被当成"模型差异"忽略)。

`units.py` 已改成:`cal/sec = 0.0041868`、`gcal/hr = 1163.0`、`kcal/hr = 0.001163`
(本来就是 IT 值,所以之前 `kcal/hr` 对、`cal/sec`/`Gcal/hr` 错,自相矛盾)。

### 8.2 ENG 单位集:已验证

同一模型切到 ENG 后重读,与 MET 版本逐流股对比:

| 量 | 单位 | 最大偏差 |
| --- | --- | --- |
| 温度 | `F` → ℃ | 2.9e-06 |
| 压力 | `psia` → bar | 7.3e-08 |
| 质量流量 | `lb/hr` → kg/h | 3e-04(相对 5e-09) |
| 摩尔流量 | `lbmol/hr` → kmol/h | 1.2e-05 |
| 体积流量 | `cuft/hr` → m³/h | 换算后与 MET 一致 |
| 热负荷 | `Btu/hr` → kW | 修正因子后 2.3e-07 |

结论:`F` / `psia` / `lb/hr` / `lbmol/hr` / `cuft/hr` / `Btu/hr` 六条 ENG 路径全部正确。

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
| 能量流(热流)的字段形态 | ✅ 已确认 | 见 12.1:`HMX_FLOW\MIXED` 就是 MW 级的能量流 |
| `X1` / `X2` 与 `X` 的区别 | ❓ 疑似子物流(substream) | 多 substream 模型验证 |
| 200–1000 流股的耗时 | ⚠️ 部分 | 已测 121 流股 / 77 模块 ≈ 9 s;按此线性外推 1000 流股 ≈ 75 s |
| 只读入口 `Apwn.ReadOnlyComposite.41.0` | ❓ 未测 | 若存在,可用于"绝不改模型"的强约束 |
| `PER_ERROR` 的确切定义 | ❓ | 两次样本 0 / 1,疑似"出错项计数",未证 |

---

## 12. 焓 / 设备连接 / 公用工程(2026-11-01 实测,第 1 步落地依据)


探测报告:`data/probe_topology.txt`(由 `tools/probe_topology.bat` 生成,脚本 `tools/probe_topology.py`)。

### 12.1 流股焓:`HMX` 是目录,真正的值在 `MIXED`

| 语义 | 路径 | 单位(实测) | 备注 |
| --- | --- | --- | --- |
| 摩尔焓 | `\Data\Streams\<流>\Output\HMX\MIXED` | `J/kmol` | **直接读 `HMX` 会拿到 `1` 这种标志位** |
| 质量焓 | `...\Output\HMX_MASS\MIXED` | `J/kg` | |
| 能量流 m·h | `...\Output\HMX_FLOW\MIXED` | `MW` | 已经是 m·h,**可直接相加做 ΔH** |

* 98/98 条流股三个字段都有值。
* 参考态任意(含生成焓),绝对值不能跨设备相加;只有同一股流进出之差才有意义。
* **不是**流股焓、别拿来算平衡的名字:
  `ENTH_CYCLE` / `ENTH_TIME`(循环 / 时间统计,单位空)、
  `L1_ENTH` / `LIQ_ENTH` / `VAP_ENTH`(分相焓,`L2_ENTH` 在纯液相流股上是空节点)。
* 读取实现:`app/aspen/reader.py` 的 `_enthalpy_leaf()`(自动把目录解析到 `MIXED`)。

### 12.2 设备连接:`IN_STRMID` 只有塔有,通用来源在流股侧

| 来源 | 路径 | 实测覆盖 |
| --- | --- | --- |
| 流股上下游 | `...\Output\SOURCE` / `DESTINATION`(值是块名) | 94/98、89/98 |
| 塔进料 | `\Data\Blocks\<塔>\Output\IN_STRMID`(元素 **Name=端口号、Value=流股名**) | 8/67(全是 RadFrac) |
| 塔进料板 | `\Data\Blocks\<塔>\Input\FEED_STAGE`(元素 **Name=流股名、Value=板号**) | 同上 |
| 换热器分侧 | `Input\FCOLD` / `FHOT`;`Output\COLDIN` / `COLDOUT` / `HOTIN` / `HOTOUT` | 9 台 HeatX 全可读 |
| 其它设备 | 没有 `IN_STRMID`(`Heater` / `Pump` / `Compr` / `MCompr` / `Sep` / `Valve` / `RCSTR`) | — |

* 同一个"设备 ↔ 流股"可能被多路看到(流股侧 / IN_STRMID / 换热器分侧),
  入库时按 `(设备, 方向, 流股)` 合并,`source` 记来源(`stream` / `strmid` / `hx` / `feed_stage`)。
* 判定"端口字段"的通用办法:**值或元素名等于一条真实流股名**。
  这样 `FCOLD=0104` 会被认出来,而 `CALC_NSHELLS=NO`、`REPORT=REPORT` 这类噪声自动滤掉。
* 边界流股:没有 `SOURCE` = 进料(4 条),没有 `DESTINATION` = 产品(9 条)。
* 读取实现:`app/aspen/reader.py` 的 `read_connections()`。

### 12.3 热负荷单位跟单位集走

同一模型里 `QCALC` / `HX_DUTY` 给 `MW`,泵 / 压缩机 `BRAKE_POWER` 给 `kW`,`HX_AREAC` 给 `sqm`。
一律按 `UnitString` 换算(见 `app/aspen/units.py`;`cal` 仍是 IT 卡 4.1868 J)。

> 实测对照(焓平衡可行性):Heater 的 `ΔH = Σ出 − Σ入(HMX_FLOW)` 与 `QCALC` 误差 **0.00%**;
> 压缩机与 `BRAKE_POWER` 差 **5.00%**(= 效率 0.95,`ΔH` 只含流体得到的那部分)。
> 所以第 2 步就用 `HMX_FLOW` 的 ΔH 对 `duty_kw`,容差 1% / 0.5 kW 是够的。

### 12.4 公用工程(`\Data\Utilities`)

样本里 7 个成员:`CW / E / HP / LP / LPG / MP / SALTW`,成员 `Output` 下:

| 字段 | 单位(实测) | 含义 |
| --- | --- | --- |
| `UTL_TCOST` | `$/hr` | 当前费用 |
| `UTL_TRATE` | `kg/hr` | 用量 |
| `UTL_PPRICE` | `$/kg`(电是 `$/kWhr`) | 单价 |
| `UTL_ENTH_IN` / `UTL_ENTH_OUT` | `J/kg` | 进出口比焓 |
| `UTL_IN_TEMP` / `UTL_OUT_TEMP` | `C` | 进出口温度 |
| `UTL_DUTY` / `UTL_USAGE` / `BLK_ID` | 数组 | **元素名 = 用到它的设备** |

设备侧的反向引用:`Input\UTILITY_ID`、`COND_UTIL`、`REB_UTIL`(`UTILITY_ID=MP` 这种)。

> HANDOFF 0.3 已拍板:只记录结构,不做公用工程识别 / 成本判断(用户模型的公用工程命名没有统一规律)。

### 12.5 焓平衡判定口径(2026-11-01 落地,`app/aspen/balance.py`)

| 设备 | 参照值 | 判据 | 备注 |
| --- | --- | --- | --- |
| Heater / Flash2 | `duty_kw`(`QCALC` / `QNET`) | `abs(ΔH − duty) ≤ max(1% × duty, 0.5 kW)` | 热负荷为 0 时按绝热规则 |
| HeatX | `HX_DUTY` | 只校验**冷侧**:`ΔH_cold = HX_DUTY` | 整体 ΔH 恒为 0(热侧给出、冷侧收取) |
| Pump / Compr / MCompr | `power_kw`(`BRAKE_POWER`) | `ΔH / 功率 ∈ [0.85, 1.05]` | 差的是效率:实测泵 1.000、压缩机 0.950 |
| Mixer / FSplit / Valve | 0 | `abs(ΔH) ≤ max(0.5% × 最大焓流, 0.5 kW)` | 绝热;4.7 kW / 20 万 kW 属噪声 |
| RadFrac / 反应器 | — | 不校验 | 塔有冷凝器 / 再沸器;反应器的 `QCALC` 不是换热负荷 |

四态:`ok` 通过 / `bad` 超差 / `no_data` 无法校验(缺流股焓值或缺参照值)/ `skip` 不校验。

三份真实模型(98/67、116/82、105/74)按此口径跑:**0 台超差**;无法校验分别为 1 / 0 / 4 台
——1 台多级压缩机没有 `BRAKE_POWER`;天大模型的 `E0102-1` / `E0102-2` 被能量流 `0136` 卡住。

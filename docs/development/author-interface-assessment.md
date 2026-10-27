# 作者单位入口的有限评估

本页结案 [#1305](https://github.com/scopecat-project/scopecat/issues/1305) 的
评估范围，基于 `7b13ce752a54e2ba19e5256b9cf565f969c6facc`。结论是**本轮不改接口**：
现有入口已有可用写法；静态签名与运行时归一化不完全等价，指定的 Quantity
输出 cast 也不能直接用 `@compute` 无损替代。这里没有新增 overload 或执行规则。
日常写法见[计算与读取指南](../how-to/compute-and-read.md)。

## 裸数与 Quantity：在哪个边界发生什么

选用现有控制声明形状：`frequency: Annotated[Input[Quantity],
ControlSpec(scannable=True)] = Quantity(5, "MHz")`。下表只讨论这个标量控制，
不把它推广成任意 Python 参数、结构参数或数组的转换规则。

| 入口 | 静态接受范围 | 本次实际观察 |
| --- | --- | --- |
| `frequency_probe(Quantity(5000, "kHz"))` | 原签名允许 Quantity | request 保留 `5000 kHz`，调用不执行实验体 |
| `frequency_probe(5)` | 原签名拒绝裸数；测试显式记录预期诊断 | 运行时仍能捕获裸数 `5`，此时没有归一化 |
| 对应两种 `.build(...)` | 同一 ParamSpec，裸数仍被静态拒绝 | 控制轴都成为 `Quantity(5, "MHz")` |
| `request.values["frequency"] = ...` | 默认字典不提供逐字段静态签名 | 编辑暂存原值；prepare 使用的 `capture_request_inputs` 将两者都变成固定控制值 `5 MHz`，不改写原 request |
| compute 消费 `Annotated[float, Unit("MHz")]`，传裸数 `5` | 符号参数接受 `ComputeArgument`，不复制原生函数的完整静态签名 | 原生函数得到 `float(5.0)`，裸数是明确消费单位下的数值 |
| 同一 float 消费契约，传 `Quantity(5000, "kHz")` | 同上；单位兼容性在构图/执行边界检查 | 原生函数得到 `float(5.0)` |
| compute 消费 `Annotated[Quantity, Unit("MHz")]`，传 `5000 kHz` | 符号参数接受 Quantity | 主机字面量及测量标量两条样例中，函数均得到 `Quantity(5, "MHz")` |
| 上述 Quantity 消费函数的 `.eager(Quantity(5000, "kHz"))` | 保留原生 Quantity 参数签名；裸数被静态拒绝 | 不进入消费归一化，函数直接读到 `.value == 5000` |

最后一行是刻意保留的原生执行边界，不是缺少一次自动换算。若 eager 函数必须
在 MHz 下计算，调用者先显式 `.to("MHz")`，或让函数实现本身承担这项契约；
不能认为 `Annotated` 在普通 Python 调用时自动转换值。

作者直接调用带 Quantity 的实验签名时，推荐显式写 Quantity；这既满足当前
静态签名，也避免让裸数的单位依赖调用者记忆。需要 GUI/脚本编辑时使用 request
值存储，并在 prepare 检查；可选 `.typed(...)` 是作者声明的字段视图，不是从
ParamSpec 自动推导的单位转换层。prepare 还负责源码身份、预览与冻结等工作；
本次只直接测试其输入捕获函数，没有启动 daemon、提交实验或证明完整 admission。

## 一个 output_type cast 的核对

只核对 `test_function_definitions.py` 中
`test_convert_preserves_host_availability_and_changes_the_unit_contract`：
低层 `ctx.compute(fn=lambda: Quantity(0.125, "V"),
output_type=ScalarType(QuantityType(unit="V")))` 明确声明 Quantity 输出 schema，
所选 overload 已经返回主机侧 `ValueRef[object]`；cast 补的是具体原生值类型
`Quantity`，并非把 ProductRef 变成 ValueRef。随后 `ctx.convert(..., "mV")`
保留 `ValueRef[Quantity]`。新证据用 `assert_type` 核对了这个信息损失。

推断路线在运行时能读取 `-> Annotated[Quantity, Unit("V")]`，生成同样的
`QuantityType(unit="V")` 与转换节点。但当前 `DeferredCompute` 的结果类型受
`ProductNativeValue` 限制，Quantity 不在此原生测量类型集合内；直接 `@compute`
装饰该函数会产生静态诊断。本次先运行未抑制的探针，确实出现装饰器参数错误，
以及把 `DataRef` 专门化为 Quantity 和后续 `assert_type` 的错误。证据测试仅对
预期的装饰器错误保留精准 ignore，并检查运行图；这不是推荐作者使用 ignore。

所以不能宣称这个 cast 已可机械删除。改为带单位的 float 返回值可以利用已有
标量推断，却改变了原生返回值契约，不是原案例的等价重写。即使普通标量推断
成功，`DataRef[T]` 仍表示 `ValueRef[T] | ProductRef[T]`，要求主机可用性的代码
仍需验证/收窄引用种类，而不能静态假定所有计算立即可用。

测量对照沿用 `test_measurement_computes.py` 的
`test_convert_preserves_measurement_availability_and_converts_native_values`：
已有 `ProductRef` 经 `ctx.convert` 仍是 `ProductRef`；测量 kernel 将
`MeasurementScalar(0.125, unit="V")` 转为 `125 mV`。其原生值为测量标量，
不是主机侧 Quantity 对象，不需要为此引入 `ProductRef[Quantity]`。

## 取舍、停止条件与证据

本轮保留显式 Quantity 输出声明及这个局部 cast，推荐普通标量走现有推断、
消费函数声明单位、eager 调用显式准备原生值。没有证据支持为了这一例扩张
`ProductNativeValue`、给全部符号调用变换 ParamSpec，或新增自动单位推断。
角频率转换、局部轴/归约、历史读取 schema 的显式责任均保持不变。

若后续确有重复的主机 Quantity 输出需求，再单独选择**主机输出的类型表达**
这一有限问题：最小验收应是此例不需 cast/ignore、测量对照仍保留 ProductRef
与原生测量值、错误单位仍拒绝，且 eager 不改变语义。如果方案必须统一两种
原生值或扩大全部符号签名，停止并重新评估。本页不启动该接口实现，也不以它
作为评估完成的条件。

本次证据是三个新小测试与六个既有测试选择，共 9 项：上述 request/build/
prepare 捕获、eager、推断 Quantity 图，原 cast 和测量转换，以及消费单位的
裸数/默认值、Quantity 字面量/测量、float 字面量归一化。新文件的 basedpyright
包含必要 ignore 检查，区分真实静态拒绝和运行时接受。没有跑完整单位矩阵。

平均 IQ、旧 shots、schema 修改/拒绝和历史重开的软件旅程复用
[#581 已记录证据](https://github.com/scopecat-project/scopecat/issues/581)，
包括 #1211 的类型边界和 #1218 的单次旅程；这里没有重跑或新增同等资格声明。
本评估不代表真人易用性、原生平台、真实仪器或完整准备/执行链的验证。

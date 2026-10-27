# 计算数值并读取类型化结果 {#compute-a-value-and-read-its-typed-result}

从一个可运行的实验开始。在实验或模块定义中调用 `@sc.compute` 装饰的函数时，
记录的是一项计算，不会在定义实验时执行函数。执行阶段，函数接收原生 Python 或 NumPy 值。
普通 Python 控制流应写在该函数内部；测量引用不是定义实验时可以直接检查的值。

本页介绍计算和类型化读取；完整的数据选择与导出见[测量数据工作流](use-measurement-data.md)，
修改计算代码后的步骤见[刷新作者代码](refresh-author-code.md)。

## 推断标量结果 {#infer-a-scalar-result}

```python
from dataclasses import dataclass
from typing import Annotated

import numpy as np
from numpy.typing import NDArray
import scopecat as sc


@sc.compute
def mean_iq(
    iq: Annotated[
        NDArray[np.complex128],
        sc.ArrayType(
            dtype="complex128",
            unit="ratio",
            dimensions=(sc.ArrayDimension("shot", None),),
        ),
    ],
) -> Annotated[complex, sc.Unit("ratio")]:
    return complex(iq.mean())


@dataclass(frozen=True)
class MeanResult[T]:
    iq: T
```

将实验返回类型声明为 `MeanResult[sc.DataRef[complex]]`。
在实验体内取得 `shots` 后：

```python
mean = mean_iq(shots)
return MeanResult(mean)
```

使用真实数组测试数值函数时，调用显式的原生入口：

```python
assert mean_iq.eager(np.array([1 + 2j, 3 + 4j])) == 2 + 3j
```

在定义之外调用 `mean_iq(...)` 会报错，并提示使用 `.eager(...)`。
在定义内部，即使实参全为字面值，调用也会记录节点，不会悄悄切换成立即执行。
原生函数体接收真实值，可以使用普通 NumPy/SciPy 代码；在其内部调用其他装饰后的辅助函数时，
也应使用这些函数的 `.eager(...)` 入口。

`sc.Unit("ratio")` 补充原生 `complex` 类型；标量形状和 dtype 由系统推断。
共享别名（例如 `type IQ = Annotated[complex, sc.Unit("ratio")]`）既可用于计算返回值，
也可用于原生数据类读取器的字段。返回注解声明存储单位，历史结果读取器精确检查该单位。
计算输入契约在消费边界对兼容单位做归一化，不会改写已存储的测量值。
普通 `float` 返回类型表示无单位计算。数组计算还需用 `ArrayType` 显式声明维度，
单独一个单位无法指定局部轴。原生数组读取器只需检查 dtype 和单位、不要求精确轴和长度时，
可使用 `Annotated[NDArray[...], sc.Unit("V")]`。

上述 complex 标量推断不需要单独的 `output_type` 或作者侧 `cast`。无单位时，返回注解可以直接写成 `-> complex`；
`bool`、`int`、`float` 和 `str` 也支持同样的推断。数组需要带有 dtype、单位和局部维度的
`Annotated` `ArrayType`。原生类型必须与声明类型一致；实数数组注解搭配复数数组 schema
会在构建时报错。

主机侧返回 `Quantity` 时，使用显式输出 schema，并补充具体引用值类型：

```python
from typing import cast


@sc.module
def voltage_module(ctx: sc.ModuleContext) -> sc.ValueRef[sc.Quantity]:
    voltage = cast(
        "sc.ValueRef[sc.Quantity]",
        ctx.compute(
            fn=lambda: sc.Quantity(0.125, "V"),
            output_type=sc.ScalarType(sc.QuantityType(unit="V")),
        ),
    )
    return ctx.convert(voltage, "mV")
```

这个低层调用的静态返回类型是 `ValueRef[object]`；cast 补充 Quantity 类型，
不会改变执行位置或转换运行时值。运行时构图也能读取
`Annotated[Quantity, Unit("V")]` 返回注解，但当前 `@compute` 的静态结果约束
不包含 Quantity，因此不能把它推荐为免 `output_type`／cast 的替代写法。
改为返回 float 会改变原生值契约，不是等价重写。测量侧的 `ProductRef` 经
`ctx.convert` 仍是 `ProductRef`，保留原生测量标量，不变成主机 Quantity 对象。

`DataRef[T]` 表示执行时才会存在的 `T` 类型值。框架根据输入选择适当的计算位置；
普通结果声明不必选择 `ValueRef` 或 `ProductRef`。直接调用支持可按位置或关键字传入的参数，
以及仅限关键字的参数，也支持默认值。仅限位置的参数、`*args` 和 `**kwargs` 会在装饰时报错。
返回类型会静态保留，`.eager` 保留完整的原生参数检查。符号调用在构图时检查参数名和必填实参；
目前还不会在静态类型层面把每个原生参数类型转换为对应的引用类型。

计算的单位由消费函数声明，不隐式继承调用它的实验。所有普通值、
Input/ValueRef 和测量 ProductRef 在进入原生函数前遵循同一规则：

- `x: Quantity` 保留输入单位；函数可显式调用 `x.to("MHz").value`。
- `x: Annotated[Quantity, Unit("MHz")]` 将兼容输入归一化为 MHz，传入 Quantity。
- `x: Annotated[float, Unit("MHz")]` 将兼容输入归一化为 MHz，传入 float。
  也可用 `Annotated[float, ScalarType(QuantityType(unit="MHz"))]`。
- 数组使用 `Annotated[NDArray[...], ArrayType(..., unit="MHz")]`，传入已换算的
  NumPy 数组；dtype、shape、轴 id/kind/单位仍需匹配。元素单位换算不会改写坐标轴。

```python
@sc.compute
def response(f: Annotated[float, sc.Unit("MHz")]) -> float:
    return 1 / (1 + (f - 5.0) ** 2)
```

`response(Quantity(5, "MHz"))` 与 `response(Quantity(5000, "kHz"))` 在构图内
产生相同结果，原生函数均收到 `5.0`。`ns` 不满足这个频率契约。
显式 float／数组目标契约下的无单位数值按目标单位解释（例如 `f=5.0` 即 5 MHz）；
这也适用于函数默认值。已有单位的输入必须兼容，不能重新贴标签。
带 ArrayType 的直接 NumPy 数组或列表会按消费契约捕获为类型化数组，
原生函数收到 NumPy 数组；构图后再修改原始容器不会改变已捕获的值。
无契约的可选 `None`、列表和数组默认值继续使用 Python 默认参数语义。
普通 float／数组参数（包括未声明单位的参数）现在拒绝带单位输入；错误指出
compute 和参数名，并提示补充契约或显式转换取数。无单位数值仍可自然使用 float。
原先先 `ctx.convert(ref, "MHz")` 再交给裸 float／数组的调用也需要补消费侧契约：
convert 改变引用单位，不移除它。

若要复用纯数值函数，可保留 `Quantity` 适配函数，在内部执行
`core(f.to("MHz").value)`。数组可在带 ArrayType 单位契约的适配函数内调用纯 NumPy
函数。不要逐元素包装 Quantity；Quantity 本身只支持单个实数。

仅支持注册的兼容线性换算；同单位无需换算。不会推导 ADC 标定、阻抗、
频率与角频率，或 offset/log 换算。整数数组仅在换算后仍为整数且不溢出时接受，
否则报错；浮点换算溢出也会报错。原测量值、dtype、单位和轴身份保持不变。

`.eager` 是普通 Python 原生入口，不经过符号构图的消费边界；直接调用时应提供
函数声明单位下的原生值。构图检查支持裸 `int`、`bool`、`str`、`float`、`complex`
参数注解；这不表示任意 Python 注解都能转换成符号契约。

### 检查发生在哪一层 {#_1}

| 作者写法 | 可检查的内容 | 仍需作者声明或验证 |
| --- | --- | --- |
| `offset.eager(1j)`，原生参数为 `complex` | basedpyright 保留原生签名，拒绝字符串、`DataRef`、缺失参数和未知参数名 | Python 运行时不自动执行类型注解检查；数值行为由函数自身和数值测试验证 |
| 构图内 `offset(ref)` | 返回值静态保留 `DataRef[complex]`；构图检查参数名、必填项及支持的 int／bool／str／float／complex 消费类型 | 普通 `complex` 等原生参数尚未全部映射成符号参数类型；静态通过不表示输入值有效 |
| `Annotated[float, Unit("MHz")]` 消费参数 | 构图拒绝不兼容单位，执行消费边界把兼容单位换算为 MHz 数值 | `.eager` 不执行该换算；不能靠实验的单位声明替代消费函数契约 |
| `rows_as(MeanResult[MeanIQ])` | 静态保留所选原生字段类型；读取时对照历史 schema 检查字段、dtype 和显式单位 | 历史读取类型需显式选择；单位校验不是单位转换，数组轴与归约方向也不能由标量返回值推导 |

裸 `int`、`bool`、`str` 的构图检查是一次有意收紧的支持边界：过去可能构图成功、
到原生执行才失败的调用，现在会在执行前拒绝。诊断包含 compute 名、参数名、期望类型和实际类型。
`Bool` 不作为 `Int`；`Float` 不会截断成 `Int`；`str` 只接受 `String`。
既有数值拓宽保持不变：`Int` 可以传给 `float` 或 `complex`，`Float` 可以传给 `complex`。
字面量、`Input`／`ValueRef`、测量 `ProductRef`、默认值和关键字实参遵守同一消费契约。
显式标量注解不能接收整个表引用；需要先选择相应单元格。未注解表参数保留原有行为。

这不是完整静态签名转换：不增加 NumPy 标量自动转换、`Literal`／`Union`／容器注解支持，
也不推断 `NDArray` 形状。`.eager` 仍遵守 Python 原生调用语义，不自动执行上述构图检查。

对应的小型可执行例子在 `test_compute_functions.py`（原生签名与符号边界）、
`test_compute_unit_boundaries.py`（消费单位）和 `test_result_rows.py`（历史 schema）。
静态负例使用精确诊断 ignore 并开启 unnecessary-ignore 检查；诊断消失同样会失败。
它们补充作者契约，不替代 installed verifier 或真人使用验证。

`experiment.compute(fn=ordinary_function, ...)` 仍可用于显式节点 ID、输出 schema、
结构化结果束和轴继承。对上述支持的标量推断，省略 `output_type` 时，它也会保留原生返回类型。
在这里复用装饰后的函数，应传入 `fn=mean_iq.eager`。

计算应返回数据，不应修改输入或控制设备。该装饰器不会追踪每个 Python 操作，
不会把 NumPy 编译到仪器上，也不提供依赖数据的设备控制流。

这个均值计算归约的是 **单个实验点** 提供的数组的所有轴，不会对实验扫描点求平均。
数组含有多个局部轴时，应在函数内显式选择目标 NumPy 轴。只保存均值会丢失 shot 分布；
如果后续分析需要不确定度或显著性估计，应保留原始 shots 或充分统计量。

## 读取时保留类型 {#keep-the-type-when-reading}

使用匹配的实验调用返回的符号结果：

```python
view = run.result(output)
means = view.rows(lambda point: point.value(view.output.iq))
```

这里的 `output` 是该次调用的 `.output`，类型为 `MeanResult[sc.DataRef[complex]]`；
`means` 的类型为 `tuple[complex, ...]`。绑定时会对照已保存的数据集检查引用。
值不可用时会报出诊断；若有意选择可用点，使用 `where_available`。
将复数值赋给 `float` 会产生类型检查错误。

包含引用的数据类描述的是将来会产生的值。读取原生行时（包括重启后），应显式选择读取类型：

```python
rows = session.run(run_id).result().rows_as(MeanResult[complex])
print(rows[0].iq.real, rows[0].iq.imag)
```

`rows` 的类型为 `tuple[MeanResult[complex], ...]`。同一个泛型数据类为符号版本和原生版本
提供相同的字段名，而不把引用当成已经存在的复数。独立的原生数据类也可使用，不必导入实验原来的
Python 模块。读取器在构造行之前验证完整的持久化字段路径和 dtype，包括嵌套数据类。
字段改名、字段缺失或标量/数组不匹配都会报错，不会填充默认值或静默丢弃数据。

普通 `complex` 读取存储单位下记录的数值大小。若还需断言单位，可复用带注解的类型：

```python
type MeanIQ = Annotated[complex, sc.Unit("ratio")]
rows = session.run(run_id).result().rows_as(MeanResult[MeanIQ])
```

数组使用 `NDArray[np.complex128]`（或其他支持的具体 dtype）。
`Annotated[..., sc.ArrayType(...)]` 还会检查精确的存储单位、局部轴名、维数和要求的长度。
数组轴名可以使用完整的持久化维度 ID，也可以使用局部名称。省略维度种类或长度表示不约束该项。
`Quantity` 将实数标量连同存储单位一起读出。此 API 不转换单位，也不缩窄数值类型；
应选择兼容的读取器，或在读取后显式变换数值。

`rows_as` 会将选中的行物化。数组仍为只读；缺失值、部分数组掩码和分段数组不能变成看似完整的原生行。
若有意选择完整点，使用 `result.where_available().rows_as(...)`；
需要掩码和不等长分段诊断时，使用带标签的数据集接口。支持的读取器使用普通初始化字段、
嵌套或参数化数据类，以及支持的原生叶类型；不会推断任意联合类型、object 字段或递归数据类。

不要重建已经改变的实验，并假定它的输出能够描述旧运行。
历史读取和投影见[测量数据工作流](use-measurement-data.md)。

## 编辑与诊断 {#edit-and-diagnose}

使用 `session = sc.notebook()` 时，默认 `live=True` 在每个单元格前选择已保存作者源码，
并在创建新请求时再次检查。新请求使用当前声明，顶层模块别名重新绑定；
已有请求、已准备实验和结果实例保留原来的身份。辅助函数别名和任意内核状态不会自动更新。

显式 `project.authoring()` 会话则按[刷新作者代码](refresh-author-code.md#explicit-author-sessions)
发布源码并重新绑定，例如 `experiment = authors.refresh(experiment)`；仅刷新服务端源码
不会替换已导入的声明。默认 Notebook 与 `live=False` 的区别及辅助函数限制，见
[Notebook 刷新路径](refresh-author-code.md#notebook-path)。

## 修复失败的计算 {#fixing-a-failed-computation}

作者计算抛出异常时，Notebook 展示的运行失败会标明计算操作、异常类型和消息；完整 traceback
保留在 worker 日志中。修复并保存函数，按所用会话的上述刷新路径选择修订，再准备新请求。
失败任务和先前成功的结果保留各自原来的身份；刷新不会重新提交失败的采集，也不会改写数据。

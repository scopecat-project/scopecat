# Compute a value and read its typed result

Start with a working experiment. A function decorated with `@sc.compute` records
a calculation when called in an experiment or module definition; it does
not execute the function while defining the experiment. At execution, the function
receives native Python or NumPy values. Ordinary Python control flow belongs inside
that function; a measurement reference is not a value you can inspect during
experiment definition.

## Infer a scalar result

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

Declare the experiment's return type as `MeanResult[sc.DataRef[complex]]`.
Inside its body, after obtaining `shots`:

```python
mean = mean_iq(shots)
return MeanResult(mean)
```

To test the numerical function with real arrays, call its explicit native entry:

```python
assert mean_iq.eager(np.array([1 + 2j, 3 + 4j])) == 2 + 3j
```

Calling `mean_iq(...)` outside a definition raises an error directing you to
`.eager(...)`. Inside a definition, even an all-literal call records a node; it
does not silently switch to immediate execution. A native function body receives
real values and can use ordinary NumPy/SciPy code. Calls to other decorated
helpers inside that body should use their `.eager(...)` entry too.

`sc.Unit("ratio")` supplements the native `complex` type; scalar shape and dtype
are inferred. A shared alias such as `type IQ = Annotated[complex, sc.Unit("ratio")]`
can be used both for the compute return and a native dataclass reader's field.
Return annotations declare the stored unit; retained readers check that unit exactly.
Compute input contracts normalize compatible units at the consumer boundary;
they do not rewrite stored measurements.
For real scalar computations with units, use `Annotated[sc.Quantity, sc.Unit("V")]`
and return a `sc.Quantity`. A plain `float` still denotes a unitless computation.
Array computations still need explicit `ArrayType` dimensions; a unit alone cannot
specify their local axes. Native array readers may use `Annotated[NDArray[...],
sc.Unit("V")]` when only dtype and unit, rather than exact axes/extents, are required.

There is no separate `output_type` or author-side `cast`. Without a unit, the
return annotation can simply be `-> complex`. The same inference supports `bool`,
`int`, `float` and `str`. Arrays need an `Annotated` `ArrayType` with their dtype,
unit and local dimensions. Native and declared types must agree; a real array
annotation paired with a complex array schema is rejected during construction.

`DataRef[T]` describes a value of type `T` that will exist during execution.
The framework chooses the appropriate computation placement from the inputs;
ordinary result declarations do not need to choose `ValueRef` or `ProductRef`.
Direct calls support positional-or-keyword and keyword-only parameters, including
defaults. Positional-only parameters, `*args` and `**kwargs` are rejected when
decorating. The result type is statically preserved, and `.eager` retains full
native argument checking. Symbolic argument names and required arguments are
checked during graph construction; symbolic calls do not yet statically transform
each native argument type into its corresponding reference type.

compute 的单位由消费函数声明，不隐式继承调用它的实验。所有普通值、
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
函数声明单位下的原生值。其他普通 Python 参数注解尚未普遍支持构图时检查。

`experiment.compute(fn=ordinary_function, ...)` remains available for explicit
node IDs, output schemas, structured bundles and axis inheritance. When its
`output_type` is omitted it also preserves the native return type. If reusing a
decorated function there, pass `fn=mean_iq.eager`.

Calculations should return data without changing inputs or controlling devices.
This decorator does not trace individual Python operations, compile NumPy for an
instrument, or provide data-dependent device control flow.

This mean reduces all axes of the array supplied at **one experiment point**. It
does not average the experiment's scan points together. If the array contains
multiple local axes, select the intended NumPy axis in the function explicitly.
Saving only the mean discards the shot distribution: retain raw shots or sufficient
statistics when later analysis needs uncertainty or significance estimates.

## Keep the type when reading

Given the symbolic result returned by the matching experiment invocation:

```python
view = run.result(output)
means = view.rows(lambda point: point.value(view.output.iq))
```

Here `output` is the invocation's `.output`, a `MeanResult[sc.DataRef[complex]]`, and `means` has type
`tuple[complex, ...]`. Binding checks the references against the saved dataset.
Unavailable values raise a diagnostic; use `where_available` when deliberately
selecting usable points. A complex value cannot be assigned to a `float` without a
type-checking error.

A dataclass containing references describes values that will be produced. For
native rows, including after a restart, select the reading type explicitly:

```python
rows = session.run(run_id).result().rows_as(MeanResult[complex])
print(rows[0].iq.real, rows[0].iq.imag)
```

`rows` has type `tuple[MeanResult[complex], ...]`. The same generic dataclass gives
the symbolic and native versions their field names without pretending a reference
is already a complex number. An independent native dataclass also works; there is
no requirement to import the experiment's original Python module. The reader
validates the complete persisted field paths and dtype, including nested dataclasses,
before constructing rows. A renamed field, missing field or scalar/array mismatch
raises instead of filling defaults or silently discarding data.

Plain `complex` reads the recorded magnitude in its stored unit. To assert the
unit as well, reuse an annotated type:

```python
type MeanIQ = Annotated[complex, sc.Unit("ratio")]
rows = session.run(run_id).result().rows_as(MeanResult[MeanIQ])
```

For arrays, use `NDArray[np.complex128]` (or another supported concrete dtype).
`Annotated[..., sc.ArrayType(...)]` additionally checks the exact stored unit,
local axis names, rank and requested extents. Array axis names may be full persisted
dimension IDs or their local names. An omitted dimension kind or extent is
unconstrained. `Quantity` reads a real numeric scalar together with its stored unit.
This API does not convert units or narrow numeric types; request a compatible
reader or explicitly transform values afterwards.

`rows_as` materializes the selected rows. Arrays remain read-only; missing values,
partial array masks and segmented arrays cannot become apparently complete native
rows. Use `result.where_available().rows_as(...)` when deliberately selecting
complete points, or the labeled dataset interface when you need masks and ragged
segment diagnostics. Supported readers use ordinary init fields, nested/parameterized
dataclasses and supported native leaf types; arbitrary unions, object fields and
recursive dataclasses are not inferred.

Do not rebuild a changed experiment and assume its output describes an older run.
See [measurement data](use-measurement-data.md) for historical reading and projections.

## Edit and diagnose

Follow [author refresh](refresh-author-code.md) after editing calculation code.
Refreshing server source does not replace declarations already imported into a
Notebook. Use `experiment = authors.refresh(experiment)` to explicitly rebind a
typed declaration and its local helpers to the newly admitted revision.

## Fixing a failed computation

Exceptions from an author computation identify the compute operation, exception
type and message in the run failure shown by the Notebook. The full traceback
remains in the worker log. Fix the function, call
`experiment = session.refresh(experiment)`, and prepare a new request. The failed
job and earlier successful results retain their original identities; refreshing
does not resubmit a failed acquisition or rewrite its data.

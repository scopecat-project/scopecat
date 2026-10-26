# 一次声明参数，用于编辑与实验 {#declare-parameters-once-for-editing-and-experiments}

作者流程从本页的参数声明开始：需要外部手工编辑时，继续阅读
[JSON 或 pandas 表格交换](exchange-parameter-tables.md)；需要将拟合结果形成候选并收集验证证据时，
继续阅读[候选验证](verify-parameter-candidates.md)。保存版本只保留参数内容，不代表科学验证通过。
不需要类的探索可以从[动态表格](declare-dynamic-parameters.md)开始。

当参数编辑、实验输入和分析候选的目标使用相同字段时，使用 `ParameterModel`。
类属性标识字段，实例读写具体值；不必另建字段常量、行常量或手工组合查询类型。

```python
import scopecat as sc


class Drive(sc.ParameterModel, table="drive"):
    qubit: sc.Param[str] = sc.param(key=True)
    duration: sc.Magnitude[float] = sc.quantity(unit="ns", default=64, minimum=4)
    pi_amplitude: sc.Param[float | None] = sc.param(default=None)
```

`Param[T]` 读写 `T`。`Magnitude[float]` 按声明单位读写数值，其实验引用携带 `Quantity`。
可选字段表示编辑中的未知值，不表示实验数值输入可以为空。类型系统不证明物理量纲或数值边界；
保存和预览使用已有的 schema 检查。

## 声明选项与项目初始值 {#declare-choices-and-initial-project-values}

用 `Param[Literal["constant", "cosine_flat_top"]]` 声明封闭的字符串选项。
同一注解同时提供编辑器补全和持久化的选项约束。`sc.param(default="constant")` 只初始化新行。
模型的文档字符串用作表格说明。

实验室维护者可以直接构建初始目录和快照：

```python
catalog = sc.parameter_catalog("lab-parameters", Drive)
initial = sc.parameter_snapshot(
    "lab-initial",
    tables={Drive: [Drive(qubit="q0"), Drive(qubit="q1", duration=80)]},
)
```

这些调用返回已有的持久化记录，可交给实验室配置构建器。空列表创建空表；可选字段的未知单元格仍然缺省。
单位转换、主键验证以及选项和边界检查复用已有的配置验证。这不会安装默认配置，也不会改变项目。

## 创建或编辑表格 {#create-or-edit-a-table}

从已有作者会话和具名参数分支打开工作区：

```python
params = author.parameters.workspace("my-parameter-branch")
drive = params.declare_table(Drive)
drive.add(Drive(qubit="q0"))
print(params.structure_diff())
version = params.save("drive-start")

row = params[Drive]["q0"]
row.duration = 80
row.pi_amplitude = None
print(params.diff())
```

创建操作必须显式执行。后续打开时使用 `params[Drive]`，不要重复添加同一行。
绑定的行与 `params["drive"]["q0"]` 共享编辑。读取时按显示单位转换，不改写存储值。
默认值只初始化新对象；选择已有行不会填充未知值。`save()` 创建保存版本；后续手工编辑仍需再次保存。

数据类和模型的表格视图都支持赋值与删除：

```python
params[Drive]["q1"] = Drive(qubit="q1", duration=96)
del params[Drive]["q1"]
```

提供的主键必须与对象主键一致，直接编辑已有主键会被拒绝。删除行会使先前选中的行视图失效。
重命名、主键更改和单位转换仍使用显式的工作区结构 API；只修改 Python 类不会迁移已保存的数据。

## 查看与复制行 {#inspect-and-copy-rows}

```python
row = params[Drive]["q0"]
print(row)
detached = row.copy()
detached.duration = 96
# The original workspace is unchanged until this explicit replacement.
params[Drive]["q0"] = detached
```

`row.copy()`、`copy.copy(row)` 和 `copy.deepcopy(row)` 生成独立的模型值。
必需字段的未知值显示为 `<unknown>`，复制后仍为未知值；读取它仍会报出明确错误。
构造函数默认值不会填充复制的未知单元格。绑定视图会拒绝主键更改，避免与模型的实验引用产生不同查询。

用 `params.scalars["repetitions"] = 128` 编辑已有标量值；它与表格编辑使用相同的差异查看、冻结和保存流程。
新增 schema 内容必须显式执行。

需要外部手工编辑时，使用 [JSON 或 pandas 表格交换](exchange-parameter-tables.md)。
导入先预览明确的变更，保留未修改单元格的来源；导入本身不产生验证证据。

## 在实验中使用字段 {#use-fields-in-an-experiment}

```python
@sc.experiment(id="lab.duration")
def duration_probe(context: sc.ExperimentContext) -> sc.ValueRef[sc.Quantity]:
    return sc.parameter_ref(Drive.duration, "q0")
```

`parameter_ref` 构建已有的符号查询，不读取当前默认配置，也不捕获 Notebook 变量。
预览和提交流程将它绑定到所选的冻结配置；后续编辑不会改变已准备的运行。
字段只有在被消费时才是必需的；未知的 pi 振幅不会阻止这个只读取 duration 的实验。

复合主键按声明顺序传值，例如 `sc.parameter_ref(Pair.duration, ("q0", "q1"))`。
各主键分量也可以使用已有支持的符号标量输入。继承字段按所选子类的表解析；
定义另一个类不会修改原类或其已保留的引用。

## 指定已保留分析结果的候选目标 {#target-a-retained-analysis-candidate}

已有托管分析结果，且其 `amplitude` 字段可作为值接受时：

```python
candidate = author.config.stage(
    fit,
    name="pi-fit",
    table=Drive,
    key="q0",
    fields={Drive.pi_amplitude: "amplitude"},
)
```

目标字段只提供身份，不提供验证证据。已保存的分析回执仍是权威来源，`None` 不能暂存为候选，
发布前仍需独立验证。来自其他表的字段会被拒绝。后续步骤见[候选验证](verify-parameter-candidates.md)。

映射到 `Magnitude` 字段的数值结果使用该字段声明的单位；`Quantity` 结果保留其显式物理单位。
编辑和候选映射使用同一兼容性规则，将声明与存储的 schema 核对。实际结果值仍来自托管回执。

## 维护者集成 {#maintainer-integrations}

编译器或策略输入需要完整冻结表时，使用 `sc.parameter_table_ref(Drive)`；
显式 schema 编辑需要列定义时，使用 `sc.parameter_definition(Drive.duration)`。
专用分析代码可以用 `sc.parameter_update(Drive.duration, "q0", 96)` 构造单元格编辑提议；
它进入已有的提议与审查流程，不会激活默认配置。

标准数据类视图仍可用于数据互操作。共享的实验室声明使用 `ParameterModel`。
原有的 `parameter_field`、`parameter_schema` 和 field/key/assignment/row/cell 句柄已在读写方迁移后移除。
已有快照和保留的运行维持原有持久化表示；移除 Python 句柄不意味着迁移存储。

## 在适配器中读取有效快照 {#read-effective-snapshots-in-adapters}

配方绑定只需要按主键投影时，使用
`sc.parameter_table(Drive).lookup(qubit="q0").select("amplitude")`（提供模型的完整主键）。
创建查询不会读取快照。其 `resolve(snapshot, {})` 返回按声明单位表示的必需值，以及来源主键和字段。
没有匹配行、匹配多行或所选单元格为未知值时都会报错。列别名使用 `.select(output_name="column_name")`。
按操作确定主键的方式见[声明式配方输入](../development/architecture/quantum-adapters.md#declarative-calibration-inputs)。

`sc.parameter_rows(effective_snapshot, Drive)` 返回使用声明单位的独立 `Drive` 行。
它通过正常 schema 验证读取所选表，不调用构造函数，也不用字段默认值填充未知单元格。
必需单元格缺值时在读取时报错；可选字段的未知值为 `None`。表不存在时报错，显式空表返回 `()`。

使用当前请求或扫描点提供的有效快照，其中包含参数单元格覆盖值。编辑这些独立行不会改变该快照或保存的参数修订。
把值保留在编译器缓存前，应将配方消费的字段冻结为不可变的编译器参数；
这些行是可编辑值，不是实时工作区绑定，也不是不可变缓存键。

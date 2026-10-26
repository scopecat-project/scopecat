# 测量数据工作流 {#measurement-data-workflows}

桌面中的文件打开、比较与导出见[打开并共享记录数据](open-and-share-data.md)。
计算结果的类型化读取入口见[计算与类型化读取](compute-and-read.md)。

Scopecat 将记录的测量数据呈现为一个带标签的 `Dataset`。其 schema 来自实验结果：
变量身份、角色、数据类型、单位、点域布局、局部维度和记录组都会保留，
无需 Notebook 代码或 GUI 根据值反推这些信息。

[实验数据流模型](../concepts/experiment-dataflow.md)说明哪些内容会持久化，以及如何声明网格、
点云、重复和遍历。本指南从已有记录开始，介绍检查、选择和转换数据的方法。

## 理解记录的形状 {#understand-the-recorded-shape}

每个数据集都有 `point` 维度。标量变量在每个逻辑点有一个值；采集轨迹或波形则在 `point`
之后增加声明的局部维度。同一记录组的坐标和观测量在这些维度上保持对齐。

schema 还会记录点来自 `product_grid` 还是 `point_cloud`。消费方使用该声明，
不会因为坐标值恰好规则就猜测它是网格。存在重复维度时，repeat 是普通的类型化点坐标。
物理遍历顺序不会改变逻辑点身份或持久化行顺序。

不可用数据会保留声明的 dtype、单位和形状，以及 `missing`、`invalid` 或 `overload` 等类型化原因，
不会被任意哨兵值替换。实验点内的计算遇到不可用测量输入时，会传播不可用状态而不调用用户计算函数；
是否过滤这些行仍由分析显式决定。

### 按实体索引的结果 {#entity-indexed-results}

多个实体返回的同质结果记录为带实体维度的一个变量，而不是每个实体一个变量。
例如返回 `PerEntity[NetworkSweepProducts]` 会产生一个 `frequency` 变量和一个 `s_parameter` 变量。
它们的形状以 `(point, logical_device, ...)` 开头；持久化维度索引保存有序的 `(kind, id)` 身份，
以及各位置对应的产物来源。

`invocation.output` 保留实验函数声明的返回类型，包括 `PerEntity` 身份映射。
`invocation.result_ref(path)` 提供返回路径的持久化句柄；需要静态类型为数组的 `RecordRef` 时，
使用 `entity_result_ref(path)`。这样既保留真实的静态作者类型，又允许记录边界将同质产物映射变为数据轴。
异质映射仍展开为结构化结果路径。分组值不属于返回树时，可显式选择同一布局：
`experiment.stack_entities(products, record_id="readout", axis="qubit")`。

按身份而不是位置选择实体：

```python
q1 = data.sel(logical_device="q1")
xds = data.to_xarray()
assert xds.coords["logical_device"].values.tolist() == ["q0", "q1"]
```

实体身份始终是完整的 `(kind, id)` 对。同种类实体轴使用可读 ID 作为 Xarray 坐标标签；
混合种类轴使用无冲突的规范身份键，并在 `scopecat_entity_labels_json` 中另存可读 ID。
有序轴还带有稳定指纹。作者声明的实体 `RecordRef` 句柄绑定该指纹，因此实体轴改变后，
旧句柄不会被静默复用。

比较按实体索引的变量前，先对齐数据集：

```python
left, right = left.align_entities(right, "logical_device", join="inner")
# join="outer" keeps the union in left-first order and masks absent leaves.
```

默认的 `join="exact"` 要求身份及其顺序完全一致。`inner` 按左侧数据集的顺序保留共有身份。
`outer` 在末尾追加右侧独有身份，对缺失的固定形状数组叶记录 `missing` 可用状态，
并将这些位置的产物来源和采集证据条目存为空值。已知显式目标顺序时，使用
`reindex_entities(dimension, entities)`。实体局部的不等长值缺失时，外连接插入局部长度未知的
`MeasurementUnavailable` 分段。它不贡献观测值，但保留请求的实体位置和缺失原因。

对于带单位的标量或数组变量，`magnitudes("mV")` 执行相同的线性换算，并保留数组形状和部分值掩码；
`require_magnitudes("mV")` 还会拒绝整行不可用的数据。

只有部分实体、shot 或 sample 叶失败时，值仍为 `MeasurementArray`，并携带只读布尔可用性掩码。
原生 Dataset 访问返回 NumPy 掩码数组，Xarray 在相同维度上输出 `<variable>__valid` 和
`<variable>__unavailable_reason` 数组，Arrow 则在嵌套数组中使用空值叶。
整个值失败时仍使用 `MeasurementUnavailable`；全部成功的值不携带掩码或附加诊断数据。

### 变长结果 {#variable-length-results}

未固定长度的采集轴是不等长（ragged）轴：

```python
from scopecat.sdk.instruments.declarations import (
    acquisition,
    array_result,
    axis,
    result_schema,
)


@result_schema
class CaptureResults:
    samples = array_result(dtype="float64", axes=("sample",))


@acquisition(results=CaptureResults, axes={"sample": axis()})
def capture_until_trigger(self) -> None: ...
```

各点可以返回不同数量的样本。Scopecat 仍验证维数、dtype 和单位；只有不等长维度的长度可以变化。
固定长度使用 `axis(size=1024)`；若由仪器状态确定一个固定长度，使用 `axis(size="points")`。

不等长数组保留每个点内的形状，绝不填充补齐。原生 Xarray 视图携带父点和局部索引坐标，
pandas 的点布局将每个数组放在一个单元格中，观测布局投影则为每个局部样本输出一行。

按实体索引的不等长变量用 `MeasurementSegmentedArray` 表示。其 `segments` 与实体轴保持对齐；
每段是一个矩形 `MeasurementArray`，或带有自身局部形状的 `MeasurementUnavailable` 值。
聚合的 `shape` 保留实体数量，在分段长度不同或未知的位置报告 `None`。
扁平化的 `values` 和 `availability` 仍可用于原生观测处理；`segments` 则保留逐实体检查和
Arrow 往返所需的分段边界。

可用的 `MeasurementArray.values` 是采用声明 dtype 的只读、C 连续 NumPy 数组。
可用标量会归一化为声明的 Python 类型。复数在 Python 中为原生 `complex`，
在 JSON 和 Arrow 结构中为 `{real, imag}` 对象。

若不可用数据使某个不等长长度未知，同一记录组中对齐的坐标或观测量可以提供该长度。
若同组其他变量也不知道长度，该点贡献零个观测值，并保留未知的局部长度。
变长轴目前属于类型化仪器采集；领域程序的结果轴仍为固定长度。

## 读取与选择测量数据 {#read-and-select-measurements}

`run.measurements()` 返回由 schema 支撑的惰性访问接口：

```python
data = run.measurements()

data.coords
data.data_vars
data.point_indices
data["readout.dc_bias"].values

near_zero = data.sel(
    **{"readout.dc_bias": sc.Quantity(0.0, "V")},
    method="nearest",
)
subset = data.isel(point=slice(0, 20), sample=slice(10, 50))
ragged_window = data.isel_ragged(
    sample=slice(10, 50),
    group="readout",
)
valid = data.where(data["temperature"].is_available())
groups = data.groupby("amplification")
```

该接口立即绑定 manifest 中的 schema，只有操作确实需要时才加载精确记录。
检查 schema 和按投影读取 Arrow 不会将整个运行物化。行访问一旦物化出快照，接口就将其与会话分离并复用它。
在此之前，行访问和 Arrow 迭代要求创建运行句柄的项目连接保持打开。

物化记录不会构建 Xarray 视图。只有 `to_xarray()`、变量的 `.xarray` 或使用 Xarray 的选择操作
才会按需构建一次该视图；Arrow 导出和普通记录访问不会产生这份分配。
公开的 Xarray 导出仍返回独立副本。Xarray 对齐特有的检查（例如同一不等长记录组内的点内长度匹配）
发生在请求视图时。

精确坐标选择保留所有匹配，包括重复的点云坐标。数值选择支持带单位的 Quantity 和可选的最近邻容差。
`isel(...)` 接受 `point` 和固定局部维度，不会移除维度。
`isel_ragged(...)` 在每个点内应用索引器，要求指定一个对齐的记录组或一个未分组变量。
布尔掩码可用 `&`、`|` 和 `~` 组合。

从实验返回值开始操作时，使用对应的类型化视图：

```python
result = run.result(spectrum.build().output)
complete = result.where_available(result.output.temperature)
rows = complete.rows(build_fit_row)
```

当记录保持原返回树形状时，`run.result(authored_output)` 绑定原始类型化句柄。
如果自动实体堆叠改变了返回树，使用 `invocation.entity_result_ref(path)` 直接访问数据集，
或使用 `run.result()` 及其持久化结果路径。后一种方式不会重建实验。
这些方式都通过 `.dataset` 提供同一数据集；若从数据集变量而不是实验返回树开始，直接使用 `run.measurements()`。
读取原生数据类行时，`run.result().rows_as(MyResult)` 验证持久化字段路径和类型，不重建作者代码。
嵌套/泛型数据类、单位、数组和可用性边界见[计算与类型化读取](compute-and-read.md#keep-the-type-when-reading)。
读取历史运行应以已保存的 schema 为准，不要用修改后的实验声明代替原始句柄。

分析通过 `context.measurements()` 获得同一个访问接口；访问它会记录精确的测量快照依赖。
运行产物和 JSON 条目是单独发布的内容，不是另一套测量 API。

## 投影到生态库 {#project-into-ecosystem-libraries}

将数据交给其他库前，用 `project(...)` 一次性绑定外部名称和目标单位。
选择器可以是持久化变量 ID、类型化结果句柄或持久化结果路径：

```python
projected = result.project(
    {
        "bias_v": result.output.dc_bias,
        "temperature_mk": result.output.temperature,
        "s21": result.output.trace.s_parameter,
    },
    units={"bias_v": "V", "temperature_mk": "mK", "s21": "ratio"},
    diagnostics="reason",
)

assert projected.units == {
    "bias_v": "V",
    "temperature_mk": "mK",
    "s21": "ratio",
}
arrow = projected.to_arrow()
pandas_frame = projected.to_pandas()
polars_frame = projected.to_polars()
xarray_dataset = projected.to_xarray()
```

投影是适配边界，不是分析框架。转换后，使用 pandas、Polars、Xarray、NumPy、SciPy 或领域库
拟合、过滤、分组和绘图。`ProjectionSchema` 为每个外部名称保留持久化变量 ID、类型化结果路径、
dtype、维度、角色、单位、标签和记录组。Arrow 在 schema 和字段元数据中携带这些语义。

转换规则固定如下：

| Scopecat 值 | Arrow / Polars | pandas `numpy` 后端 | Xarray |
| --- | --- | --- | --- |
| 点标量 | 声明的标量类型 | 原生标量列 | `point` 变量 |
| 点内数组 | 固定或变长列表 | 每行一个 NumPy 数组 | 具名局部维度 |
| 部分可用数组 | 含空值叶的嵌套值 | NumPy 掩码数组 | 填充值及逐元素有效性/原因 |
| `complex128` | `{real, imag}` 结构 | 原生复数标量或数组 | 原生复数数组 |
| 不可用值 | 空值及可选诊断 | 缺失值及可选诊断 | 填充值及诊断 |

`to_pandas()` 默认输出熟悉的 NumPy/原生数值大小。pandas 不为这些值提供可靠的单位 dtype，
因此应在投影边界显式选择每个带单位字段的输出单位。选择与来源相同的单位也有意义：
它记录该数值单位是公开分析时有意做出的选择。若继承单位，`to_pandas()` 会警告，
列出受影响列及其实际单位。`projected.implicit_units` 可在不物化 DataFrame 的情况下执行同一检查。
若需要 pandas 保留 Arrow 扩展 dtype，使用 `dtype_backend="pyarrow"`。

声明的坐标单位是实验面向的科学单位，不是硬件传输偏好。例如，T1 轴可保持 `us`，
同时目标在绑定时将每个值转换为整数 `ns` 样本。不要只为简化底层编译就把作者声明的轴归一化为后端单位；
这也会改变默认的测量单位和投影单位。

`diagnostics="reason"` 为每个所选字段输出稳定的 `<name>__unavailable_reason` 列；
`"full"` 还输出 JSON 元数据。默认包含身份列，可用 `identity=False` 省略。

投影选项一起提供，以便对照最终的身份列、诊断列和布局列检查名称：

```python
observations = result.project(
    {
        "bias": result.output.dc_bias,
        "frequency": result.output.trace.frequency,
        "s21": result.output.trace.s_parameter,
    },
    units={"bias": "V", "frequency": "Hz", "s21": "ratio"},
    diagnostics="reason",
    identity=True,
    layout="observations",
)
```

`layout="points"` 为每个实验点保留一行，局部数组表示为嵌套 Arrow 列，或 pandas 中的 NumPy 数组。
显式的 `"observations"` 布局将一个对齐的局部数组组展开为标量行：点标量被广播，
局部维度获得 `<dimension>_index` 列，数组坐标和观测量仍保持对齐。
所选数组必须具有相同的局部维度，并属于同一记录组；不兼容的选择会被拒绝，不会各自独立展开。

pandas NumPy 后端的标量整数、布尔值和字符串使用 pandas 可空扩展 dtype，即使某一批没有空值也是如此。
浮点值使用 `float64`；诊断可区分表示不可用的 NaN 和有效的科学 NaN。
复数仍是原生 Python 或 NumPy 复数；不可用的标量观测使用 `None`。

### 增量读取大型投影 {#read-large-projections-incrementally}

使用同一投影创建有限的 Arrow 读取器，无需物化整个运行：

```python
reader = (
    run.measurements()
    .project(
        {"bias_v": "readout.dc_bias", "s21": "readout.s_parameter"},
        units={"bias_v": "V"},
        diagnostics="reason",
    )
    .to_record_batch_reader(batch_size=500)
)
for record_batch in reader:
    analyze_arrow(record_batch)
```

对于基于运行且未经切片的数据集，读取器向 daemon 请求投影后的 Arrow 分页。
对于已物化或已切片的数据集，它拆分本地投影表。空数据集保留 schema，但不产出记录批次。

`batch_size` 限制存储的实验点数量。观测布局可能将一个点展开成多行；返回的 Arrow 批次仍有大小上限。
第一页固定当前已持久化的点数，后续追加数据属于之后创建的读取器。
这是有限快照，不是实时订阅。实时游标、检查点、重试和完成通知属于未来的工作流流式契约。

小分页可能重复访问同一个存储 Arrow 块。daemon 的运行仓库复用已验证的块字节，
缓存载荷上限为 16 MiB，最多 32 个条目；淘汰最近最少使用的条目，不保留超大块。
这限制的是保留的缓存载荷，不是总进程内存或调用方解码后的数组。
冷读取仍会加载完整的不可变 blob 和其原生 Arrow 块。
行、变量和实体投影发生在将选中值复制到 NumPy 之前；这不是存储层的实体过滤下推。

打开读取器前，选择一个已声明的实体维度：

```python
from scopecat.kernel.entity import EntityRef

wanted = (EntityRef(kind="qubit", id="q7"), EntityRef(kind="qubit", id="q0"))
for run in runs:
    reader = (
        run.measurements()
        .project({"signal": "signal"}, diagnostics="full")
        .select_entities("entity", wanted)
        .to_record_batch_reader(batch_size=10)
    )
    for batch in reader:
        analyze_arrow(batch)
```

每个运行独立读取。选择保留请求的 `(kind, id)` 顺序，不受存储顺序影响。
已有实体保留存储的描述和来源产物。原生选中记录和 trace 响应也保留采集证据；
Arrow 表使用已有的可用性诊断和 schema 来源信息，不额外增加采集回执列。
缺失实体保留请求中的描述，生成带 `entity_alignment="absent"` 的不可用值，不伪造采集证据。
这与 `Dataset.reindex_entities` 使用同一对齐方式，是单维度选择，不是多个运行的连接操作。
HTTP Arrow 和 trace 接口每次查询最多接受 32 个选中实体，包括缺失身份；点分页大小有独立上限。

Arrow schema 元数据携带所选测量 schema、`scopecat.run_id`、`scopecat.config_content_hash`
和第一页的 `scopecat.snapshot_size`。读取器为后续分页固定该点数。
刷新操作台 trace 面板会发起另一次有限查询，不会订阅实时更新。

选择限制复制的值数组和序列化响应的宽度，但仍可能读取完整 blob 字节、原生 Arrow 缓冲区，
以及宽的元数据/证据附加数据。`entity-reads` 组件基准将这些成本与选中值字节数、
序列化 IPC 载荷字节数、采样进程 RSS 分别报告。缓冲区大小不是内存峰值测量。

复用以当前解析出的内容摘要为键，不以运行局部路径为键。缓存命中时检查文件身份、大小和变更时间戳；
未命中时使用正常的 SHA-256 验证。缓存字节不可变。这依赖对象存储的不可变文件契约，
不把时间戳当成内容摘要的密码学替代。引用查找和读取器固定点数的语义保持不变。
不存在跨项目共享的进程全局缓存。

### 使用原生带标签数组 {#use-native-labeled-arrays}

每个变量通过 Scopecat 自己的同一接口提供值、标签、有效性、失败原因和单位：

```python
temperature = data["temperature"].dense
assert temperature.layout == "dense"
valid_temperature = temperature.values[temperature.valid]
temperature_mk = temperature.to("mK")

waveform = data["waveform"].observations
assert waveform.layout == "ragged"
assert waveform.declared_dims == ("point", "sample")
parent_points = waveform.coords["readout__sample__parent_point_index"]
```

`Variable.labeled` 适用于两种布局。`Variable.dense` 拒绝不等长数据，
`Variable.observations` 拒绝密集数据，因此代码不会静默混淆矩形张量和扁平化的带索引观测流。
`LabeledMeasurementArray.isel(...)` 和 `.sel(...)` 保持标签与诊断对齐。
其 NumPy 值、有效性掩码、原因和坐标均为只读。

### 使用 Xarray 视图 {#use-the-xarray-view}

`Dataset.to_xarray()` 保留 Scopecat 带标签的点域和不等长布局，不经过表格投影边界：

```python
xds = data.to_xarray()
another = data.to_xarray()
assert xds is not another

grid = data.to_xarray(layout="grid")
```

默认布局保留持久化的 `point` 维度，适用于点云、部分选择和不等长结果。
对于完整的乘积网格，`layout="grid"` 按乘积顺序将作者声明的轴恢复为维度。
它会拒绝不完整网格、重复或缺失序号，以及不匹配声明网格轴的坐标。

同一记录组中的不等长变量共享一个观测维度，带有父点和局部索引坐标。
观测有效性掩码和不可用原因坐标使整数、布尔值和字符串的填充值不产生歧义。
导出前应通过 Scopecat 接口选择点；直接调用 `data.to_xarray().isel(point=...)`
不会级联选择独立的不等长观测维度。

测量值和元数据是深度不可变的。每次 `to_xarray()` 都返回独立快照，调用方的编辑不会影响后续选择或导出。
完整的 Arrow、DataFrame 和 Xarray 转换会物化所选快照；Notebook 内存放不下时，使用投影后的记录批次读取器。

## 按实体身份选择轨迹 {#select-traces-by-entity-identity}

对于按实体索引的采集，分析代码可以按持久化身份选择轨迹，无需查找数组偏移：

```python
traces = dataset.traces("iq", entities=(sc.EntityRef(id="q1", kind="qubit"),))
```

选择匹配 `(kind, id)`，保留存储的实体元数据和采集证据，遇到缺失身份会报错。
轨迹点索引始终是原数据集的索引，过滤或重排后也是如此。
这是从已物化的数据集中提取选定轨迹，不表示服务端过滤或有界内存流式读取。

## 让 GUI 使用同一 schema {#let-the-gui-use-the-same-schema}

规范 schema 在首次追加数据之前就已注册，因此 GUI 能展示实时数据集和空数据集，无需从 JSON 值猜测结构。
角色、维度、记录组、标签、单位、dtype 和点域布局共同确定安全的表格、标量、轨迹、散点和乘积网格视图。
复数支持幅值、相位、实部和虚部模式。执行时，最新接收点以 Arrow IPC 到达浏览器；
大型轨迹无需先转成 JSON 数组再渲染。

自动绘图请求有界投影，并标注不完整的实时曲面。无法安全自动绘图时，类型化表格仍可使用。
具体绘图候选的选择和浏览器限制属于 UI 及其测试维护的展示实现细节，不是科学数据契约的一部分。

## 单独发布分析输出 {#publish-analysis-outputs-separately}

测量数据集是不可变的运行输入。拟合数据集、事实、图形、报告和参数提议，
只有通过附属于该运行的显式分析发布才会持久化。
输出分类、无损原生库边界、修订和可选执行证据见[分析发布](../concepts/analysis-publication.md)。

## 为分析命名采集维度 {#name-acquisition-dimensions-for-analysis}

量子程序可能保留 `shared/rabi-program/shot` 这样的 shot 轴。
通过所选字段为导出维度命名，不要依赖该内部路径：

```python
view = run.measurements().project({"amplitude": "amplitude", "iq": "iq"})
data = view.to_xarray(dims={"iq": ("shot",)})
mean_iq = data["iq"].mean(dim="shot").values
```

名称按顺序描述字段中密集的非 point 轴。即使只有一个固定点，也会保留 `point` 维度。
共享同一来源轴的其他所选字段获得相同名称。共享轴名称冲突、将独立轴合并的名称，以及与已有变量重名都会报错。
两个独立采集可以显式使用 `shot_a` 和 `shot_b`。

这是 Xarray 导出选项。原始维度、Arrow/DataFrame 导出和投影 schema 保持不变。
导出的 Dataset 在 `scopecat_projection_json` 中保留来源投影，在 `scopecat_dimension_aliases_json`
中保留来源到导出维度的映射；二者都是适合 NetCDF 的 JSON 字符串。
不等长观测布局保留已有身份，目前拒绝这种密集轴命名选项。不会移除命名空间或隐式对齐。

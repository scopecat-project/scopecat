# 复制并编辑普通 Python 实验 {#copy-and-edit-an-ordinary-python-experiment}

已有实验室操作可以组合成普通作者实验。你编辑输入控制、科学辅助函数、时序与分析；
实验室维护者一次性配置发现入口，不需要为每个实验另写目录、服务或 procedure。

第一次使用请求、扫描和分析时，先完成[入门作者课程](../tutorials/starter-authoring.md)；
无设备练习从应用的[帮助入口](../tutorials/teaching-sandboxes.md)开始。旧参考示例库不是作者模板。

## 组合实验室操作 {#compose-laboratory-operations}

在实验室已注册的作者目录或起始项目中工作。同一 Python 声明提供控制项名称、单位、
取值来源和边界。组合已安装适配器提供的操作，不必复制参考应用的编译器或配置。

在实验模块中可以：

- 修改已声明输入的默认值、边界或响应辅助函数。
- 组合实验室库支持的时序操作。
- 修改分析函数，改变数据选择或拟合模型。
- 在作者文件夹内复制函数或文件，并使用不同实验函数名或显式、唯一的装饰器 ID。
  导入或重新导出的实验不会被重复发现；重复 ID 会报错，不会任意选择一个。

共享实验室操作继续作为库调用，除非你准备维护相应能力。普通实验编辑不要求接管其编译器、
物理映射或驱动。发现过程读取函数输入契约，不执行函数体。
标量输入（`str`、`int`、`float`、`bool` 或同一种标量类型的 `Literal`）
可以不设默认值，由启动表单或 `authors.prepare(..., inputs={...})` 提供。
不归控制项管理的标量 `sc.Input[T]` 运行时输入也适用。没有数值控制项的实验无需 `ControlSet`。

严格带单位输入也可直接显示数值和单位两个字段，例如：

```python
from typing import Annotated

@sc.experiment
def voltage_input(
    experiment: sc.ExperimentContext,
    amplitude: Annotated[
        sc.Input[sc.Quantity], sc.QuantityType(dimension="voltage")
    ] = sc.Quantity(2000, "mV"),
) -> None:
    pass
```

该输入的 JSON 是 `{"amplitude": {"value": 2000, "unit": "mV"}}`。
作者发现校验默认值；准备时拒绝裸数、错误量纲、非有限值及超出声明边界的值。
表单、已保存草稿和预览保留显式单位，执行按声明校验并在需要时换算单位。
这仅支持 `Input[Quantity]`，不开放任意对象表单，也不改变 `ControlSpec` 的数值输入语义。
扫描仍通过显式声明 `scannable=True` 的控制项和 `.over(...)` 使用。

命名标量别名也可用于这些输入。例如 `type Target = Literal["q0", "q1"]` 配合
`target: Target`，可共享可读契约，并在启动表单保留相同选项。

以下声明在尚未选择 qubit 时就能出现在目录中：

```python
@sc.experiment
def selected_qubit(experiment: sc.ExperimentContext, qubit: str) -> None:
    experiment.use(my_lab_operation(qubit=qubit))
```

结构性输入仍是普通 Python 值；`sc.Input[T]` 在函数体中提供符号运行时值。
复杂 Python 对象由维护方的组合代码处理。输入或控制编辑无效时，准备阶段会指出字段；
实验体内部错误则在构建程序时报出，不在导入声明时触发。
成功发现或刷新源码不证明所有结构性输入都能构建有效实验。

## 从 Python 运行和分析 {#run-and-analyze-from-python}

标量测量可以是复数，例如逐 shot IQ 的均值。dtype 和单位在保存与重开后仍保留；
存储一个复数无需改成数组。`compute` 可从辅助函数的 `-> complex` 推断无单位标量。
具有科学含义的单位仍须显式声明，数组也一样；具体写法见[计算与类型化读取](compute-and-read.md)。

测量组装失败时，等待中的作者 job 会收到底层异常类型和消息。
保留失败 job 回执和运行证据；完整 traceback 可从 worker 日志读取。
设备意外异常仍遵守独立的信息清理策略。

注册作者目录保留完整作者修订。显式作者会话中，保存编辑后刷新，再准备与提交；
无需 Git commit、手写哈希或重启设备服务：

```python
import scopecat as sc

project = sc.open_project("/path/to/author-workspace")
with project.authoring() as authors:
    authors.use(parameter_branch="experiment", setup=authors.setup.get("bench-a"))
    observed = authors.state()
    authors.refresh(expected_generation=observed.generation)
    launch = authors.prepare("signal", actor="alice")
    print(launch.preview.point_count, launch.preview.code_revision)
    submitted = launch.submit(request_key="sample-a-signal-001")
    print(submitted.procedure_id)
```

预览固定完整源码修订和精确配置。重试同一次提交必须保留同一请求键和已准备内容；
提交结果未知时先核对原始提交，不要换新键重新采集。`procedure_id` 是任务身份，
不是其子运行的 run ID；从已接纳任务取得精确子运行身份后，可在同一数据空间用
`authors.run(run_id)` 读取保留结果，不按“最新运行”猜测。

GUI 的 **固定值**、**扫描列表** 和 **扫描范围** 编辑同一 `ControlSet`。
固定点与单点扫描即使对应同一物理点，也保留不同意图；修改控制项会使旧预览失效。

在托管作者会话中，用 `signal(...)` 创建请求，编辑其 `values`，再调用
`authors.prepare(request)`。默认 `sc.notebook()` 的已保存源码选择与显式作者会话的刷新
有所不同，见[刷新作者代码](refresh-author-code.md)；旧/新修订分析保留数据也在该页说明。

维护者可用 `signal.build(...)` 显式构建本地调用，再使用低层 lab API。
保留运行支持 `run.analyze` 及受支持的分析实参。普通直接导入不会自动热重载；
仅声明身份的来源记录不能等同于完整的修订感知路径。

## 在 GUI 中使用同一声明 {#use-the-same-declaration-in-the-gui}

保存新增或修改的作者文件后，选择 **刷新作者代码**。选择实验并编辑控制项，按以下顺序检查与提交：

1. 确认参数、实验配置、对象和记录集等本次选择。
2. 选择 **预览**，审查点数、配置和执行前检查。预览不采集。
3. 选择 **开始采集**。开始操作也会先检查并准备当前输入；旧预览失效时不能沿用其证据。
4. 提交确认后选择 **查看已提交任务**，再从任务打开它实际接纳的子运行，保留完整 run ID。
   后续 Python 分析复用这条精确记录。

提交保留普通单次运行 procedure 和精确子运行，沿用已有请求键与配置代次检查；
作者无需另行声明 procedure。若界面显示 **原始提交待确认**，先用 **核实原始提交**
查询原请求。`准备新的 run（独立采集）` 表示另一采集意图，不能用于掩盖未知结果。

daemon 不导入作者模块。新 worker 验证完整候选并原子发布，另一个新 worker 预览或执行该修订。
刷新失败会显示源码错误，并保留此前可用目录。已接纳或运行中的作者任务保留原辅助函数、
实验和分析源码；历史分析显式选择归档修订。数据格式要求与外部环境限制见
[刷新与恢复边界](refresh-author-code.md)。

## 一次性注册作者目录 {#one-time-author-directory-registration}

维护者在工作区 manifest 注册作者包：

```toml
[authors]
modules = ["my_lab.authored"]
source_roots = ["src"]
```

已安装的实验室适配器提供执行能力。普通作者注册不要求自行组装 `LabApplication`，
见[项目布局](../reference/project-layout.md)。

需要显式组合和检查时，可从 `scopecat.application.authoring` 使用
`AuthorExperiments.discover(...)`。发现只接受已配置模块/包，不接受 HTTP 任意传入的模块名。
应用将生成的目录项和 procedure 定义与维护方已有 provider 组合，两者的实验 ID 必须不同。

修改可复用科学语义属于共享能力维护者；增加 lowering、硬件映射、设备协议或执行保证属于
编译器/驱动维护者。[探索验收约定](../development/reference-fixtures.md#exploratory-author-fixture)
将这些职责与普通实验编辑区分开。

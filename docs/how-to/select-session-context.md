# 选择实验上下文 {#select-an-experimental-context}

作者会话可以为后续请求记住样品、实验批次、参数分支、记录集合和操作人。
这些选择属于当前客户端。连接同一 daemon 的两个 Notebook 可以各自选择，
不会改变设备控制权或另一客户端的选择。

```python
session = sc.notebook()
setup = session.setup.get("my-bench")
collection = session.create_record_collection("Chip A · cooldown 3")
session.use(
    sample="chip-a",
    batch="cooldown-3",
    parameter_branch="chip-a/daily",
    setup=setup,
    collection=collection.id,
    operator="Li",
)
params = session.params

prepared = session.prepare(rabi())
run = prepared.run().wait().result()
session.history()
session.run(1)
```

示例中的样品、批次和分支须已存在，创建与编辑见[参数分支](parameter-branches.md)；
`rabi` 是从项目导入的实验。`project.authoring()` 和 `AuthorProject` 也提供同一个 `use` API。
选择只做读取与验证，不运行实验、不激活配置，也不证明标定有效。
选择独立参数或测量对象不要求已有可执行 setup；准备运行时，预览再检查设备兼容性与执行支持。

## 只改变一项选择 {#change-one-choice-without-resetting-the-others}

```python
session.use(operator="Wang")
session.use(parameter_branch="chip-a/trial")
selected = session.selection
```

省略的字段保留原选择。切换参数分支保留对象和批次；选择样品或目标则清除原批次。
参数值、setup 和尚未保存的分支编辑仍可用于新对象：

```python
session.use(sample="chip-b")
session.use(target=exact_target_ref)
```

未知参数修订或不存在的集合会被拒绝，失败的更新保留全部原选择。
`selection` 是不可变快照。后续 `use` 只影响未来的准备；已准备请求、已提交任务和保留结果
仍使用原选择。共享配置发生变化时，正常的服务端预览与接纳检查仍然生效。

在 IPython 中，`sc.notebook()` 复用当前 kernel 的会话；单元格边界的源码刷新保留选择。
关闭后重新打开会话从空选择开始，不把 selection 保存为应用级“当前样品”。
共用一个 kernel 的多个 Notebook 也共用会话，如同共用 Python 变量。

## 只覆盖一次准备 {#override-one-preparation}

工作台的 **本页测量上下文 → 选择参数分支** 可以采用 Notebook 保存的参数版本，
同时保留页面的对象和批次，并要求重新预览。刷新分支列表不会自动替换选中版本，见
[参数分支](parameter-branches.md)。

显式 `prepare` 参数仍可覆盖本次准备：

```python
session.prepare(rabi(), parameters=other_parameters)
session.prepare(rabi(), parameters=params, sample="chip-b", batch="cooldown-4")
session.prepare(rabi(), parameters=params, actor="Guest", record_collection=None)
```

独立分支编辑器提供参数值，并继承会话的对象与批次。显式 `sample` 或 `target` 替换本次对象，
并重置批次，除非也显式提供批次。未保存的值会冻结到运行中，不推进分支。
编辑器不能与 `selection` 或单独的 `overrides` 同时使用。

`candidate` 自带科学范围，样品和批次必须与其证据一致。只选择 `sample` 会建立新范围；
只改变批次则保留其他选择。操作人和集合独立继承，除非显式覆盖；本次清空集合表示使用默认记录集合。
这些覆盖都不修改 `session.selection`。

保存计划已有冻结的科学范围。`session.prepare_plan(ref)` 使用该范围，只继承当前操作人和集合，
两者均可为本次执行显式覆盖。计划保留原批次，不采用会话默认批次；显式 `batch` 参数是一项断言，
必须与保存范围一致。

## 编号查询跟随所选集合 {#number-lookup-follows-the-selected-collection}

选中集合后，`history()`、`run(number)` 和 `run_number(run)` 都以该集合为范围。
查询另一集合的运行编号会报错。持久字符串 run ID 不受该选择影响。

传入 `collection=another_id` 可指定查询集合；`collection=None` 使用原有数据空间全局编号/历史，
不修改会话选择。未选集合的会话仍使用全局行为。稳定地址和当前格式恢复见
[记录集合](record-collections.md)。

批次范围见[实验批次](experimental-batches.md)，登记对象与执行限制见
[测量目标](register-measurement-targets.md)。设备选择与参数归属独立；
集合名为“cooldown 3”不证明此前标定适用于这次降温。

## 在工作台选择上下文 {#select-context-in-the-workbench}

在 **实验** 页，**本页测量上下文** 保存样品和操作人。
展开 **浏览样品、批次与记录集** 可选择登记样品、实验批次和记录集合。
列表中的 **Load more batches / Load more collections** 分页加载；
**New batch / New collection** 创建命名条目，创建后再明确选择使用。
创建元数据不会开始测量或改变另一页面的选择。

GUI 实验草稿与 Notebook kernel 的临时 selection 不同。界面将科学选择、操作人和
记录集合随实验输入保存到应用数据；确认已保存的草稿可在页面刷新或应用重启后恢复。
切换实验、刷新作者代码或切换视图不会主动清除这些输入。**重置实验草稿** 会保存采用声明默认值的
新输入修订，并解除计划关联，保留页面的样品、批次、记录集合和操作人。

关闭前应等待“实验输入已保存到应用数据。”。尚未确认保存或保存失败的输入不能承诺在
进程丢失后恢复；请保持窗口打开并重试保存。多个窗口各自编辑，发生并发冲突时会保留
独立冲突副本，需要明确选择并保存，不能把它们当作共享可变默认值。数据空间及工作区
边界仍然有效，连接另一项目不会将原项目草稿变成新项目的选择。

恢复输入要求核实并重新准备，不恢复旧预览或执行资格；未知的原始提交保留自己的请求身份，
恢复只查询，不会因刷新或编辑草稿而重发。GUI 草稿保存不代表 kernel selection 能跨重启保存。

在 **实验配置** 中选择参数版本并点击 **用于下次实验**，再在实验页选择 setup。
对象和批次保持独立；没有标定证据支持时，参数值只是初始估计。

打开保存计划会恢复它冻结的样品修订和批次，不采用页面原有科学范围；集合和操作人仍取自目标页面。
候选输入同样保留来源科学范围，操作人和集合是独立的目标选择。

每次选择编辑都会使预览失效。**预览** 使用与 Python 相同的启动契约验证；只有 **开始采集** 才接纳过程。
提交响应丢失时，后续编辑不会丢失原请求。这些选择均不发布新的共享默认配置。

声明批次和集合目前要求标准 `@experiment` 作者实验。旧自定义启动 provider 会在预览报告不支持的选择；
工作台不会丢弃这些选择来强行启动，其过程适配器必须先显式支持该范围。

## 检查科学选择 {#inspect-the-scientific-selection}

`session.selection.science` 包含对象、配置选择与显式批次范围。
`prepared.preview.reviewed` 包含精确绑定及已检查的配置来源。
普通作者可继续使用 `sample`、`target`、`parameters`、`setup`、`batch` 简写参数。
直接构造请求的客户端使用 `ScientificSelection`，不能与这些便捷参数混用；
提交或保存请求必须携带预览的已审阅证据。

## 软件执行场景 {#software-execution-scenarios}

可执行 setup 可以声明软件场景。标签、模型身份、覆盖范围、限制、实际使用的随机种子和设置
都属于 setup 身份。普通入门项目声明虚拟温度与解析信号响应；实验室适配器可声明自己的有限模型。
没有场景表示**未声明**，不是物理执行的证据。

在 **实验配置** 中检查当前或所选 setup 修订。
`prepared.preview.reviewed.binding.scenario` 是本次准备冻结的场景，历史运行保留自己的科学绑定。
切换 setup 或刷新源码不改写这些证据。软件声明会拒绝配置仪器注册表中任何非虚拟连接；
这是可信 provider 的契约，不是任意 Python 驱动的操作系统沙箱，也不自动模拟不支持的硬件。

为页面或 Notebook 选择受支持的 setup。实际执行能力由已安装 provider 提供，选择软件场景不会替换 provider。
参数分支仍然独立；证据检查防止软件结果被用于不兼容的物理 setup。

模型声明描述适配器实际行为。仅在声明中加入种子或设置，不会让任意驱动采用它；
不要声称 provider 未实现的物理模型或确定性随机流。

## 从适配器配置模板开始 {#start-from-an-adapter-configuration-template}

工作台 **实验配置 → 配置模板** 列出当前实验室适配器提供的完整配方。
导入前阅读说明与软件模型覆盖范围。导入在同一事务中保存新的不可变 setup 和独立参数修订，
不创建组合配置条目，也不将任一项设为全局默认。模板是初始配方，不是标定有效性证据。

为当前页面或 Notebook 采用导入参数与 setup；没有全局 setup 激活操作，其他页面保留各自选择。
共享设备连接变化后，开始前应针对新连接重新预览。

Notebook 使用相同操作。以下 `lab` 已连接当前服务，`session` 是它的作者会话：

```python
templates = lab.setup.templates()
for template in templates:
    print(template.id, template.label, template.description)

chosen = templates[0]  # Choose after inspecting the available recipes.
imported = lab.setup.import_template(chosen, name="software-trial")
session.use(selection=imported.selection)
prepared = session.prepare(experiment())
```

`imported.selection` 包含精确参数与 setup 引用，不虚构样品或标定结论。
要搭配另一 setup，显式调用 `session.use(setup=other_setup)`；源码刷新保留选择。
相同名称和导入意图再次导入会返回相同记录；模板或意图变化时须使用新名称。
导入参数可通过 `lab.parameters.get(name)` 和 `lab.parameters.list()` 访问。
若要保留会话当前 setup，使用 `session.use(parameters=imported.parameters)`，它也保留对象与批次。
参数记录本身不归属于某个 setup。

仅提供运行中适配器声明的模板；选择软件标签不会替换仪器后端或把物理连接变为虚拟连接。
没有模板的适配器不会提供配方。本地安装设置仍是部署输入，不是日常场景选择器。

## 理解预览拒绝 {#understand-a-rejected-preview}

预期的编译与配置检查经过作者 worker 后仍保留问题代码、消息和位置。
工作台连同所选软件场景的模型与限制一起显示这些发现。
能力检查失败表示所选 setup 无法满足该操作，不证明该仪器系列或框架永远无法支持它。
例如缺少 flux 路由的 setup 需要补足匹配路由，或选择另一个实验。

Notebook 收到带相同结构化诊断的 `AuthorLaunchRejected`：

```python
from scopecat.records.launch_rejection import AuthorLaunchRejected

try:
    prepared = session.prepare(experiment())
except AuthorLaunchRejected as error:
    print(error)
    for problem in error.diagnostic.problems:
        print(problem.code, problem.location, problem.details)
```

预览拒绝不会提交采集。修正输入或选择合适 setup 后重新准备；预期检查失败不要求重启作者 worker。
新预览失败时，界面清除此前成功预览。普通 Python 错误与结果未知的提交失败保留原诊断，
不会被改称“不支持的模型能力”。

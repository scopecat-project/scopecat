# 刷新实验、辅助函数与分析 {#refresh-experiments-helpers-and-analysis}

项目可以把实验声明与驱动放在不同的普通 Python 模块中。作者代码刷新为新任务选择代码；
修改常驻设备驱动则使用单独的空闲设备更新流程。
[计算与类型化读取](compute-and-read.md)介绍计算代码，[测量数据工作流](use-measurement-data.md)
介绍结果读取；本页说明修改代码后如何选择新修订，同时保留旧请求和历史运行的源码身份。

Notebook/IPython 中只需建立一次 `session = sc.notebook()`。已保存的编辑会用于新的实验请求，
两种常见导入方式都可使用，新模块在下一个单元格可用。
参见 [Notebook 工作区指南](../tutorials/teaching-sandboxes.md#notebook-workspace-and-saved-edits)。
以下显式操作仍适用于脚本和需要控制源码选择的场景。

不需要设备的项目 `testing/fixtures/retained-signal` 启用了作者修订。
在其副本中编辑 `src/ui_signal`，然后在 **Experiments** 中选择 **Refresh author code**。
Scopecat 先在新进程中验证完整源码快照，再发布它。预览更新后的控制项，按正常流程提交。
仪器服务继续运行，已接受的过程保留原有代码。只改辅助函数时，控制项编辑和扫描会保留，但预览失效。
声明变化时，保存的编辑会保留供审查；新字段获得当前默认值，被删除的字段在显式移除前仍处于未解决状态。
再次准备前检查恢复的输入，详见[启动输入恢复](declare-experiment-controls.md#keep-a-launch-draft-while-inspecting-the-project)。

语法或导入失败会显示文件和 traceback，先前的目录仍可使用。修复文件后再次刷新。
GUI 和显式作者会话需要显式刷新：只保存文件不会选择新定义。
`sc.notebook()` 则默认在每个单元格前选择已保存源码，具体见下文。
刷新检查源码导入和声明契约，不执行实验体。预览检查所选输入构建出的程序；
刷新成功本身不代表所有结构性选择都已验证。并发刷新会比较观察到的代次；
竞争失败的刷新必须先检查新状态，再重试。

## Notebook 路径 {#notebook-path}

使用 `session = sc.notebook()` 时，默认的 `live=True` 在每个单元格前选择已保存作者源码，
并在创建新实验请求时再次检查。`from module import experiment` 和 `import module as experiments`
都可使用：新请求选择当前声明，顶层模块别名会重新绑定。新保存的模块在下一个单元格可用。
源码验证失败会阻止新请求，直到问题修复；历史读取和修复用单元格仍可使用。
`sc.notebook(live=False)` 关闭后续已保存编辑的自动选择。

已有请求、已准备实验、结果实例和历史源码保持固定。这不会重新加载每个 Python 对象：
辅助函数别名和任意内核状态不会自动更新。已安装依赖发生变化时，仍需匹配的环境和内核重启。

## 显式作者会话 {#explicit-author-sessions}

脚本或需要控制源码选择的场景中，使用 `project.authoring()` 提供的修订感知连接，
在编辑作者文件后显式刷新：

```python
import scopecat as sc

project = sc.open_project(".")
with project.authoring() as authors:
    observed = authors.state()
    # Edit an experiment, imported helper, or analysis in your editor.
    refreshed = authors.refresh(expected_generation=observed.generation)
    launch = authors.prepare("signal", actor="alice")
    print(launch.preview.point_count, launch.preview.code_revision)
    submission = launch.submit(request_key="sample-a-signal-001")
```

首次使用和保存编辑后，都使用同一个连接/导入单元格：

```python
session = project.authoring()
session.refresh()
from ui_signal.ordinary import signal

request = signal()
launch = session.prepare(request)
```

`refresh()` 发布已验证源码，并同步本地作者导入缓存。此后正常导入的模块级实验会携带该已接受修订。
新实验函数和新模块也遵循同一流程：保存文件，运行 `session.refresh()`，再正常导入。
日常流程不需要 `load_experiment` 或手工 `importlib.reload`。

刷新后重新运行导入行，才能更新其 Python 变量。先前绑定的别名、请求和结果实例保留原有定义。
编辑前创建的请求，即使稍后才准备，也保留原来的默认值和源码。
要使用新定义，重新导入并创建新请求。在刷新之前导入，看不到保留源码快照之外新添加的文件。

已有实验变量也可用单个表达式 `signal = session.refresh(signal)` 更新绑定。
`load_experiment` 是恢复时使用的高级精确版本绑定操作，不是首次加载的必需步骤。
移除的关键字会在构造新请求时报错，因此改变签名或结果数据类时，应同步更新调用单元格并处理编辑器类型错误。

`source_roots` 下的项目本地模块会被替换。导入使用校验和验证过的归档字节，包括相邻辅助函数和资源；
后续工作区中未保存或已保存的编辑都不会渗入该次导入。已安装包和任意 Notebook 状态不会重新加载。
更新已安装依赖后应重启 Notebook。重新绑定期间不要并发执行项目导入。
本地 Python 调用中的动态导入遵循 Python 当前导入表；需要修订拥有的执行时，使用
`authors.prepare(request)`，不要把旧 Notebook 对象当作隔离的历史解释器。

语法和服务端验证失败会保留先前的活动修订。如果服务端发布成功，但 Notebook 无法导入，
异常会标明该修订，本地模块绑定会回滚，也不会准备请求。
修正本地环境后再次刷新（高级恢复可用 `load_experiment` 绑定精确修订）；
不要假定服务端发布会随本地导入一起回滚。

等待超时或连接断开时，异常的 `operation` 保留准备操作的身份。
完成该操作并绑定其精确结果，无需再次发布：

```python
state = interrupted.operation.reconnect(authors).wait(timeout=120)
signal = authors.load_experiment(signal, code_revision=state.active)
```

重试同一次提交时，应保留请求键。预览保留用于检查的代码修订和配置；
刷新另一个修订不会替换该提交中的实现。普通作者仍编写已有的 `@sc.experiment`、`ControlSet`
和 `@sc.analysis_step` 声明，不必为每个实验维护服务、过程或目录适配器。

`authors.prepare()` 接受与 GUI 启动相同的类型化 `context`、`overrides`、`inputs`、`control_edits`
和 `sample` 字段。传入已保存的 `ConfigContextRef` 和一组 `ParameterUpdate` 元组，
可在不改变实验室默认配置的情况下探索其他工作点。预览将该上下文的精确样品修订和覆盖值与代码修订一同保留；
提交与 GUI 启动使用同一个共享解析器。

分析保留数据时，应显式选择运行 `author_code_revision` 元数据中的原始修订，或新刷新的修订：

```python
from scopecat.records.author_revision import AuthorRevisionRef

with project.connect() as lab, project.authoring() as authors:
    retained = lab.get_run(saved_run_id)
    original = AuthorRevisionRef(
        content_hash=retained.request.metadata["author_code_revision"]
    )
    result = authors.analyze(
        retained.id,
        "ui_signal.ordinary:selected_mean",
        code_revision=original,
        key="original-model",
    )
    publication = retained.published_analysis(result.analysis_id)
```

这只对保留数据执行分析。除已有分析追踪外，发布记录还以 `author_code_revision` 保存完整源码修订。
显式选择刷新后的修订，会使用新辅助函数和分析定义产生一次独立分析。
普通函数和 `@sc.analysis_step` 声明都接受托管 `arguments`，并按所选源码的注解和默认值验证。
对于普通类型化结论，`authors.analyze_as(...)` 默认使用运行的原始源码，也支持通过 `source="current"`
使用选中的当前源码。支持的实参类型和已保存发布结果的只读重开方式见[普通分析](../guides/ordinary-analysis.md)。

`project.connect()` 保持已有的单代码根进程契约。作者编辑使用 `project.authoring()` 和显式类型化重新绑定；
若要从另一个冻结代码根直接加载应用，应使用新进程。
未选择修订的普通直接 Python 执行仍会记录声明身份，但不声称记录了完整辅助函数来源。

## 声明简单表单输入 {#declare-simple-form-inputs}

支持的普通 Python 参数会与控制项一起成为表单字段，无论是否有默认值。
例如夹具中的 `signal` 实验声明了 `polarity: Literal["positive", "negative"] = "positive"`。
在 Experiments 中选择 polarity，或向 `authors.prepare()` 传入 `inputs={"polarity": "negative"}`。
同一声明创建目录 schema，并在绑定实验前验证输入值。有效实参（包括默认值）保留在已接受的意图中；
后续刷新不会替换这些实参或原始实现。

结构性输入使用 `str`、`int`、`float`、`bool`，或同一种标量类型的 `Literal` 选项。
无默认值的参数成为必填字段；发现过程不会构建实验，也不要求伪造默认值。
在准备前通过 `inputs` 提供值，或在创建请求时通过类型化实验调用传值。
缺失或无效值在程序构建前就会验证失败。发现过程会明确点名并拒绝可空联合类型、容器和其他复杂对象；
维护者仍可通过已有 Python API 组合这些对象。
受控输入的值应放在 `control_edits`，而不是 `inputs`。固定和扫描控制项保留单位、边界和已有编译器验证。
普通作者修改支持的参数时，不需要更新另一套逐实验解析器或目录。

## 维护者配置与支持边界 {#maintainer-configuration-and-supported-boundary}

维护者在 `scopecat.toml` 中一次性配置这些路径：

```toml
[authors]
modules = ["ui_signal.signal", "ui_signal.ordinary", "ui_signal.analysis"]
source_roots = ["src"]
```

`source_roots` 归档完整的本地依赖树，包括辅助函数、分析和本地资源文件，不需要单独声明刷新目录。
`modules` 使用已有装饰器和控制项选择可发现的实验模块或包，并与 `lab.capabilities.author_modules`
或已安装适配器提供的标准模块组合。作者自己选择的模块使用 `authors.modules`：新增或修改该列表后可以刷新；
不同注册作者文件夹可选择不同列表和编译器代码，同时共享应用。所选本地模块必须位于源码根内。
精确选择保留在每个修订中，因此旧计划和运行保留原来的目录。
自定义 `lab.application` 工厂不能与 `authors.modules` 组合；这种组合应使用声明式 capabilities。
没有 `[authors]` 的项目保留已有初始加载行为，不显示刷新按钮。

刷新会重新读取 manifest，并捕获所有声明的根，包括共享科学辅助函数和编译器代码。
每个已准备任务保留其精确快照。准备期间的编译器错误可以修正并刷新，不必重启应用；
已准备任务仍保留旧代码。这不会切换常驻驱动 worker。
修改驱动后，应先结束设备会话，再使用 **Devices and drivers → Installed drivers → Update from source**，
或 `lab.devices.update_driver_source(path)`。已安装依赖变化仍需经过资格验证的环境更新。
所有本地 Python 依赖必须属于声明的源码根；符号链接、环境和缓存被排除。
任意外部源码安装、运行时生成的导入和实时补丁不在恢复契约内。

修订记录 Python 和已安装分发包的版本。这些是必需的环境检查，**不是可复现容器或已归档的依赖安装**。
恢复时需保留匹配的安装产物。状态根仍是当前项目；worker 从单独物化、通过校验和验证的不可变代码根导入。
历史调度先读取已接受修订，再导入任何项目代码，然后使用已有的精确过程定义注册表。
刷新绝不替换正在运行的 worker，也不提供任意失败过程的自动迁移。

## 存储与备份边界 {#store-and-backup-boundary}

准备操作与不可变修订及实验计划一同存储。
[基线前数据策略](../development/data-compatibility.md)适用：当前构建不读取或迁移早期开发格式。
若维护归档环境，应单独保留历史文件。

在当前格式内，[备份与恢复](backup-and-restore.md)保留源码包、manifest 身份、活动代次、准备结果
和原始已接受过程意图。daemon 重启或恢复后，未完成的准备记录变为 `interrupted`，
不会悄悄使用今天的文件重新运行。恢复支持的工作前，应恢复记录中的外部环境和匹配的维护源码。

## 长时间准备与重连 {#long-preparation-and-reconnecting}

源码准备没有隐含的墙钟时间上限。新环境或较慢机器上的原生库冷导入可能耗时更久。
`authors.state()`、`authors.prepare()` 和同步 `authors.refresh()` 会按需等待初始源码准备。
等待过程报告最后观察到的阶段和操作 ID；GUI 刷新展示状态和已用时间，并提供取消操作。

希望控制 Notebook 等待时长时，使用操作句柄：

```python
from scopecat.daemon.preparation import AuthorPreparationTimeout

operation = authors.begin_refresh()
print(operation.id)  # Keep this ID to reconnect from another session.
try:
    refreshed = operation.wait(timeout=20)
except AuthorPreparationTimeout:
    print(operation.status())  # Preparation continues in the daemon.

# Later, including from a new authoring connection:
operation = authors.preparation(saved_operation_id)
refreshed = operation.wait()  # Same captured source; no resubmission.
```

`wait(timeout=...)` 只限制调用方等待。Ctrl+C 也只结束等待，不取消准备。
观察连接断开会抛出 `AuthorPreparationDisconnected`，并保留同一 `.operation` 句柄；
重连后可查明 daemon 已完成还是重启。使用 `operation.cancel()` 请求取消，随后检查 `status()`，
直到进入终态。`cancelling` 表示候选进程清理仍未完成。
发布前取消会保持活动修订不变；发布后取消返回已有的成功结果。
发布和成功回执在同一事务中提交；即使后续 worker 采用该修订失败，回执仍表示源码发布已经成功。

提交传输错误会抛出 `AuthorPreparationSubmissionUncertain`，携带 `.operation` 和 `.request`。
先检查原始操作 ID；如果找不到，`authors.begin_refresh(operation_id=..., expected_generation=...)`
可以重新提交同一请求。已接受的 ID 始终返回其最初捕获的修订和结果。
不要用新 ID 处理未知结果。失败、取消和中断的操作仍可检查；修正原因后，应显式开始新刷新来捕获编辑后的文件。

daemon 健康响应只确认 API 存活，不代表作者目录已准备就绪。
首次 GUI 目录请求仍可能超过 HTTP 调用方的等待时间；应检查刷新面板中的准备状态，不要直接认定失败。
短启动调用（目录、预览和提交）保留显式的 60 秒 worker 预算，从初始准备结束后开始计时。
历史 worker 恢复、预览执行以及采集/设备截止时间与源码准备分开，不会因此变为无上限。

查看 `.scopecat/daemon.log` 获取原生导入诊断。验证过程安排在 30 秒后输出一次线程栈，不含局部变量，
正常退出时取消该输出。失败证据保留有界 stderr 尾部。报告的阶段或栈只能说明观察到 worker 所在的位置，
不能证明死锁或杀毒软件原因。不会自动重试，也不会回退到其他源码。

**实验提交超时的结果是未知的**：响应丢失前，请求可能已被接受。
保留原始请求键，使用提交恢复查找该次接受记录。源码准备 ID 和实验提交键标识不同操作。
目录加载和预览本身不会提交采集。

## 已安装的实验室方法 {#installed-laboratory-methods}

工作区可将捕获的本地实验与维护的 wheel 包组合：

```toml
[authors]
source_roots = ["src"]

[authors.packages]
lab_methods = "scopecat-lab-methods"
```

键是顶层 Python 模块名，值是已安装分发包名。应用仍需显式注册发现，例如
`LabApplication(author_modules=("lab_methods.experiments", "user_experiments"))`。
分析选择接受这些声明包或本地源码根中的模块。声明包不会自动发现其中所有实验。
已安装代码和本地代码应使用不同顶层名称，避免遮蔽。

除分发包版本外，修订还保留已声明模块树（包括资源）的内容哈希。
新增文件和同版本替换会改变身份，生成的字节码不会。这些是已安装内容的哈希，不是 wheel 归档哈希。
恢复时保留匹配的 wheels 和环境。含有已导入辅助函数的其他实验室包也需声明；
未声明的第三方依赖仍按 Python/分发包版本检查，不检查其文件内容。这不是封闭自足的环境归档。

这里只支持从 wheels 安装的普通包和单个 Python 模块；可编辑安装的分发包和命名空间包会被拒绝。
维护者探索时，将可编辑代码放在项目捕获的本地源码根中，部署时再切换为 wheel。
worker 运行时不要修改已安装文件：刷新只针对本地代码。
升级共享方法是环境操作，需要重启并匹配修订/部署；按原始源码分析时，必须恢复原始已安装包的字节。
已有 worker 不会实时监控文件系统完整性。

## 稳定工作与试验工作 {#stable-and-trial-work}

试验值使用独立参数分支，即使两个注册源码文件夹最初读取相同参数修订也是如此。
源码文件夹、参数分支和装置配置独立选择；刷新试验代码既不推进稳定参数分支，也不替换设备驱动。
准备运行时会冻结所选源码和值，后续编辑不改变已准备的运行。
编辑 `author.params` 可使会话所选分支与保存同步。独立打开的 `author.parameters.workspace(...)`
是另一个 checkout；要使用其保存的分支头，应将它传给 `prepare(..., parameters=...)`，
或显式重新选择该分支。其他会话的保存绝不会悄悄改变本会话的选择。

预览按实验声明的字段、主键和单位检查所选参数目录。只有字段名相同不能证明兼容。
应修正不兼容的试验声明，或显式准备匹配的试验参数；不要为使试验预览通过而改变稳定分支。
Python 类型和单位相同的语义变化无法自动推断：为改变的概念赋予显式字段/契约身份，不要静默复用名称。
原始源码分析继续使用运行保留的代码。

## 执行依赖范围 {#execution-dependency-scope}

维护者可以将执行依赖与 Notebook 工具分开选择：

```toml
[authors]
source_roots = ["src"]
dependencies = ["scopecat-instruments", "scipy", "my-analysis[fit]"]

[authors.packages]
lab_methods = "scopecat-lab-methods"
```

Scopecat 及其 server 始终包含在内，已声明的安装式作者包也一样。
Scopecat 传递地跟踪其已安装 `Requires-Dist` 元数据，计算平台/Python 标记和请求的 extras。
它按已安装版本检查要求，不安装或解析新环境。分发包名称会归一化。
缺失或不兼容依赖会使准备失败，并标明依赖名。这不会检查 Python 导入：
维护者必须声明本地代码、可选执行路径和分析使用的依赖，包括 extras。
包声明仍决定源码归属；只列出依赖并不授权导入其中的分析。

保留的 manifest 记录精确的所选版本。升级无关的 Notebook 专用包不会改变修订，也不会阻止历史分析。
托管作者文件夹应在 `pyproject.toml` 的 `project.dependencies` 中声明新增依赖，
并在 Settings 使用 **Prepare background dependencies**。随后刷新以使用准备好的环境；
已有准备工作和计划保留各自依赖版本，应用重启后也一样。
本地 pip 安装只影响用户内核，见[应用维护](maintain-application.md)。
根目录 `pyproject.toml` 会被捕获，其项目依赖扩展 `authors.dependencies`。
根目录 `uv.lock` 和 `requirements.txt` 只在未限定依赖范围的环境中捕获；源码根内的文件仍会被捕获。

两个依赖声明都省略时，会保守地保留完整环境清单和根目录安装文件，适合尚未声明依赖边界的探索项目。
空列表显式选择仅框架、声明的作者包及其依赖。新 CLI 起始项目自动选择其仪器依赖。
恢复当前格式修订时允许存在额外已安装包，但每个记录的版本仍必须匹配。
该检查不增加早期 manifest 或存储格式的读取能力。

这仍是环境兼容性检查，不是封闭自足的归档或导入沙箱。Python 必须匹配；
原生库、驱动和外部系统依赖仍属于部署资格验证。实验室自有的导入辅助函数需显式声明包，才能保留内容哈希。

# 从实验请求逐步绑定仪器

普通仪器实验可以先查看能力需求，再从已登记设备的声明中选择端点，无须先在 GUI
建立 setup。Python 与启动界面使用同一需求、候选和检查服务。正式准备仍保留精确的
execution setup；这个入口不取消设备策略、科学参数或实验检查。

## 支持范围

首期支持能够直接构建符号程序的普通仪器实验：每个端口一个接口，无实体选择、
无嵌套模块或领域执行，角色为默认或显式命名。单台 VNA 不需要虚构 q0 或用途名称。
两个相同接口且角色相同的端口需要作者明确区分用途；不会自动选第一个设备。

需求检查在所选作者修订中构建符号声明，尚未进行参数解析、领域编译或完整程序准备。
依赖 topology、实体、嵌套模块或领域编译的实验返回 `supported=False` 和诊断；
请提供已有明确 setup，不能把不完整需求当成可执行清单。声明构建失败也保留错误原因。
这是正常作者代码的声明流程，不是任意 Python 副作用的沙箱。

候选来自登记连接修订和驱动来源的纯声明，不连接设备、不读前面板、不下发状态、
不获取控制权。声明未知时保留未知；候选不表示物理接线、可用性或校准资格。

## Python 最小路径

在已准备的作者目录和匹配执行环境中保存普通实验，例如：

```python
import scopecat as sc
from scopecat_instruments import NetworkSweepProducts, network_sweep


@sc.experiment
def capture(experiment: sc.ExperimentContext) -> NetworkSweepProducts:
    vna = network_sweep(experiment)
    vna.ensure(points=51)
    return vna.sweep()
```

以下假定已经登记一台 **Virtual VNA** 并完成 starter 参数初始化。实际设备需独立核对
设置、连接和安全策略，不能照搬模拟策略。`device_id` 从 `author.devices.list()`
查看；不会为绑定自动登记设备或创建默认配置。

```python
from scopecat.records.instrument_bindings import InstrumentBindingPlan
from scopecat.records.execution_scenario import SoftwareExecutionScenario

# author 是已有的 sc.notebook() 或 project.authoring() 会话。
author.refresh()
from scopecat_lab.authored.capture import capture

author.use(parameter_branch="starter", setup=None)
request = capture()
requirements = author.binding_requirements(request)
print(requirements.supported, requirements.diagnostics)
options = author.setup.binding_options(requirements)
print(options.candidates, options.diagnostics)
```

查看并明确选择对应设备及端点。下面的匹配是作者的显式选择；候选不自动替作者做决定。
`select` 复制候选的精确身份，不需要手工复制能力 ID 或连接修订。

```python
device_id = "my-registered-virtual-vna"  # 替换为已核对的登记 ID
matches = [
    c for c in options.candidates if c.device_id == device_id and c.component_path == ()
]
if len(matches) != 1:
    raise ValueError("请核对设备、端点与声明诊断")
chosen = matches[0].select(
    run_start="preserve",
    success_action="release",
    failure_action="abort_and_release",
)
plan = InstrumentBindingPlan(
    requirements=requirements,
    choices=(chosen,),
    scenario=SoftwareExecutionScenario(
        id="virtual-vna",
        label="Synthetic VNA",
        model_id="scopecat.virtual.vna",
        model_version="v1",
        capabilities=("Synthetic network sweep",),
        limitations=("Not a physical measurement or calibration qualification",),
    ),
)
checked = author.setup.check_bindings(plan)
print(checked.diagnostics)
if checked.definition is None:
    raise ValueError("先修正绑定，再准备")
prepared = author.prepare(request, instrument_bindings=plan)
print(prepared.preview)
# 独立检查预览后才显式提交：
job = prepared.run()
run = job.wait(timeout=120).result()
```

检查仅验证声明端点、选择完整性及已有设备安全策略，**不表示全部可执行**。
所有生命周期策略必须显式选择，登记设备的安全要求仍然有效。prepare 重新核对需求来源、
设备 head 和声明，再走已有解析、完整检查及快照流程。连接修订发生变化时拒绝旧选择，
请重新查看候选，不自动改绑。

绑定参数与已有 exact setup 不能同时选择。此例只清除当前会话的 setup 选择，
不会修改全局默认或其他会话。prepare 创建执行用的保留定义/修订，不把它加入具名复用
setup 列表。它不会切换当前参数分支或数据空间。

## 修改和按需保存

普通输入和扫描直接修改请求，再次准备。不同的设备端点需要重新明确选择并检查；
旧 prepared、receipt 和历史配置不追随新选择。已准备后设备连接变化仍受既有提交围栏
约束，不保证旧 prepared 可以在变更后的设备上提交。

需要长期复用时，显式保存检查结果，再选择该 setup：

```python
saved = author.setup.save(checked.definition, name="bench-vna", note="Reviewed mapping")
author.use(setup=saved.ref)
prepared = author.prepare(request)
```

`setup.get("bench-vna")` 根据当前设备 head 解析；`setup.revision(saved.id)` 只读原精确修订。
保存仍不表示能运行。设备有新连接、驱动或配置时，应重新检查并准备；历史结果继续使用
原来源。地址、实验参数、校准证据与物理接线各有自己的核对范围，绑定不证明它们有效。

## 共用传输契约

- `POST /api/v1/experiment-launcher/bindings` 接受 `LaunchRequest(action="bindings")`，
  返回 `BindingRequirements`，包含来源、输入身份、支持状态和端口需求。
- `POST /api/v1/setup/bindings/options` 返回声明候选与无匹配、需选择、声明未知诊断。
- `POST /api/v1/setup/bindings/check` 接受 `InstrumentBindingPlan`，返回 setup 草稿或诊断。
- preview 可携带 `instrument_bindings`。缺 setup 的普通参数请求返回 422，
  `LaunchRejection.bindings` 含需求报告；其他原有拒绝仍可能是文本或普通 problems。
- 执行阶段使用 `/setup/bindings/resolve` 保留 execution-only setup；该步骤属于准备，
  不是候选查询或完整可执行性检查。submit 使用 reviewed 的精确配置，不重新选择设备。

GUI 应直接消费这些诊断和候选，不从显示名称或路径字符串推断物理能力。

## 在启动界面选择本次设备

选择普通作者实验及参数，尚未选择 setup 时，可使用 **检查本实验所需设备**。
界面发送当前实验、输入、源码修订和工作空间，不另行猜测需要的能力。
每项需求必须明确选择已登记设备的端点及开始、成功、失败策略；即使只有一个候选也不会自动选择。
软件模拟应显式填写模型、能力与局限，不把虚拟结果标为物理测量。

**检查并用于本次准备** 使用上述同一后端检查服务。通过后仍需点击 **预览** 或 **开始采集**，
由既有准备流程重新核对需求、设备修订与完整实验输入；绑定检查本身不连接设备、不提交实验。
复杂拓扑、实体或嵌套模块等不支持范围会显示诊断，应改选明确配置的 setup。

本次端点选择保留在当前打开的编辑器中，改变输入、源码或离开编辑器后需重新检查；
不把未保存的选择冒充可恢复的命名配置。需要复用时展开 **另存为可复用 setup**，
明确填写名称并保存。保存不会切换其他页面或开始采集，已有运行继续使用原精确快照。

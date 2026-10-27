# 区分降温等实验批次 {#keep-cooldowns-and-other-experimental-batches-distinct}

实验批次标识一次物理事件，例如一次降温或装样。它独立于芯片身份、参数分支、记录集和代码目录。
物理条件变化时创建新批次；只重命名标签不会改变已有证据的适用范围。
手动线路列表记录依次切换哪些线路及各次尝试，不能代替实验批次。

以下示例使用已连接原作者目录的 `session`（如 `sc.notebook()`），以及已有样品 `chip`。

```python
batch = session.create_experimental_batch("芯片 A · 第三次降温")
```

用 `session.experimental_batches()` 浏览保留的批次，
用 `session.experimental_batch(batch_id)` 读取指定批次。同名可以共存，ID 稳定。
目录支持有限分页和乐观元数据编辑，类似[记录集](record-collections.md)；批次不提供删除操作。
GUI 在**实验 → 本页测量上下文 → 浏览样品、批次与记录集**中选择，
详见[测量上下文](select-session-context.md#select-context-in-the-workbench)。
创建或读取批次只是元数据操作，不启动测量。

## 分别选择参数与批次 {#select-parameters-and-batch-independently}

使用已有样品 `chip` 和参数分支 `chip-a/daily`：

```python
session.use(
    sample=chip.id,
    batch=batch.id,
    parameter_branch="chip-a/daily",
)
params = session.params
```

分支保存参数版本，不拥有样品，也不证明参数适用于某次降温。
另一批次可以选择同一组保存值，无需复制工作点；在相应测量验证前，这些值只是初始估计。
若需分开保存后续试调，用 `params.save("chip-a/cooldown-3-trial")` 派生分支，
再显式选择它，见[参数分支](parameter-branches.md)。

选新样品会开始新的科学范围，应像上例那样同时指定批次及参数分支。
只改批次会保留当前参数选择：

```python
next_batch = session.create_experimental_batch("芯片 A · 第四次降温")
session.use(batch=next_batch.id)
```

实验室的可执行配置（setup）另行选择。预览检查设备与输入是否兼容；
选择批次或保存参数不激活设备，也不证明校准适用。

## 准备并保留实际范围 {#prepare-and-retain-the-actual-scope}

先选已有 setup，再准备本次实验并保存计划。`signal` 是作者目录中已登记的实验：

```python
collection = session.create_record_collection("芯片长期研究")
session.use(batch=batch.id, collection=collection.id)
prepared = session.prepare("signal")
plan = prepared.save_plan("第三次降温信号", saved_by="operator")
```

以上尚未采集。检查预览并决定开始后，再显式执行：

```python
run = prepared.run().wait().result()
assert run.samples[0].batch_id == batch.id
```

以后修改会话不会重标记这次准备或结果。换批次也不重置编号；若要独立编号，另选记录集。
名称和目录路径都不会自动推断这些选择。

独立参数编辑器提供数值，同时继承会话的样品和批次；也可在一次准备中显式指定样品/批次。
保存的计划则保留冻结的科学范围：

```python
session.use(batch=next_batch.id)
reopened = session.prepare_plan(plan.ref)
assert reopened.request.selection.batch.id == batch.id
```

此处只是重新预览，不会再次执行。向 `prepare_plan` 显式传入冲突的
`batch=next_batch.id` 会被拒绝。candidate 同样保留来源证据的样品及批次，
不能重标记到另一降温：使用 candidate 准备会选择该保留范围，
直接运行接纳也拒绝冲突的样品/批次选择。
探索新条件时，应选择独立参数估计并取得新证据；不会追溯改写旧结果。

## 校准证据与历史 {#calibration-evidence-and-history}

校准检查评估精确测量输入，包括参数或 candidate 身份、对象、setup 和执行场景。
样品绑定中保留的批次属于证据范围。分支名或成功保存不是可复用的校准结果。
检查适用性与发布流程见[自动参数校准](automate-parameter-calibration.md)；
改批次标签不会创建新证据。操作者归属与执行授权也各自独立。

按批次过滤保留运行，不改变运行地址：

```python
from scopecat.records.research_project import RunHistoryFilter

page = session.list_runs(history=RunHistoryFilter(batch_id=batch.id))
```

当前格式保留批次目录及运行/批次关联。未指定的批次仍未指定，不从分支名、目录或时间猜测。
[当前格式备份与恢复](backup-and-restore.md)保留这些证据。
旧格式只支持明确的 **115→116** 显式升级，具体操作与限制以
[数据兼容策略](../development/data-compatibility.md#what-remains-supported-now)为准；
这不新增其他旧格式迁移或读取承诺，也不保证旧环境可重执行。

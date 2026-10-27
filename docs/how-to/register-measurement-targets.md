# 注册精确测量目标 {#register-exact-measurement-targets}

测量目标记录参与的样品修订，以及带成员限定名的实体如何连接。
Python 可注册和检查单样品或 assembly 目标。Notebook 会话和工作台可为作者实验、预览与保存计划
选择精确单成员目标；多成员 assembly 执行尚不支持。注册不证明接线已通过物理验证。

以下使用已连接的 `LabClient` 和已注册样品。`A` 这样的成员 ID 只在该目标内有效；
`A/q0` 与 `B/q0` 是不同实体。

```python
from scopecat.records.scientific_scope import MeasurementTarget, TargetMember
from scopecat.records.target_catalog import (
    TargetCreateCommand,
    TargetReviseCommand,
    TargetRevisionDraft,
)

sample = lab.samples.revision("chip-a", 1)
member = TargetMember(
    id="A",
    sample_id=sample.sample_id,
    revision=sample.revision,
    content_hash=sample.content_hash,
)
registered = lab.create_target(
    TargetCreateCommand(
        catalog_id=lab.health().project_id,
        target_id="chip-a-measurement",
        draft=TargetRevisionDraft(
            name="Chip A",
            content=MeasurementTarget(members=(member,)),
            actor="Li",
        ),
    )
)
exact = registered.ref
assert lab.resolve_target(exact) == registered
```

目录 ID 是已有持久数据空间身份，目前由 `health().project_id` 提供。
重启、移动代码目录及受支持备份恢复均保留该身份。另一目录的引用会被拒绝，
即使本地 target ID 碰巧相同。目标成员属于本地目录；复制外部样品 ID 不是导入或身份映射。

`lab.target("chip-a-measurement")` 明确读取最新修订。
`lab.target("chip-a-measurement", revision=1)` 读取保留的本地修订；
`lab.resolve_target(exact)` 还检查目录归属与内容哈希，不以 latest 替代不匹配的精确引用。
`lab.targets(limit=100, before=cursor)` 分页列出各目标的最新修订。

修订目标时提交已审查的精确版本。过期引用会冲突，应重新读取并审查，不覆盖另一编辑者的修改：

```python
renamed = lab.revise_target(
    TargetReviseCommand(
        expected=registered.ref,
        draft=TargetRevisionDraft(
            name="Chip A / mounted",
            content=registered.content,
            actor="Li",
            note="Clarify the display label",
        ),
    )
)
assert renamed.ref.revision == 2
assert renamed.ref.content_hash == registered.ref.content_hash
assert lab.resolve_target(exact).name == "Chip A"
```

每个修订（包括名称和审计元数据）都不可变。内容哈希覆盖科学成员与连接，
只改名称不改变该哈希，但修订号仍递增，且仍是精确引用的一部分。
创建和修订都有明确冲突语义：网络结果未知时先读取目标，再决定是否重提；
不会自动重试过期修订。

注册 assembly 时，增加另一个 `TargetMember` 和 `TargetConnection`，
端点可分别为 `TargetEntity(member_id="A", entity_id="q0")` 与
`TargetEntity(member_id="B", entity_id="q0")`。
服务端在同一注册事务中验证每个样品修订/哈希，并对照保留的样品拓扑检查实体。
验证失败不写入目标或修订；成功注册也不触发执行、标定发布或设备采集。

目标目录属于当前数据格式。[备份与恢复](backup-and-restore.md)保留目录身份，
但独立可写克隆没有受支持的合并策略。历史格式读取和升级范围以
[数据兼容性策略](../development/data-compatibility.md)为准；注册本身不增加迁移能力。

## 在 Notebook 中选择目标 {#use-a-target-in-a-notebook}

```python
session.use(target=exact)
prepared = session.prepare(rabi())
plan = prepared.save_plan("Target check", saved_by="Li")
run = prepared.run().wait().result()
assert run.snapshot.scientific_binding == prepared.preview.reviewed.binding
```

`rabi` 是当前工作区的作者实验；准备前须选好兼容参数与 setup。
`use(target="chip-a-measurement")` 也接受目标 ID 字符串，并在选择时解析为精确修订。
刷新源码不会推进该目标版本；使用新版本需显式重新选择。

预览固定目标引用、保留内容、实体投影、样品修订、批次和精确配置证据。
提交和保存计划保留该绑定，重开计划不会替换为 Notebook 当前目标。
保留候选已有自己的配置与对象证据，不能用新 setup 或 subject 覆盖，见
[测量上下文解析](resolve-measurement-context.md)。会切换配置的维护方多阶段参考流程仍要求样品选择。

## 在工作台选择目标 {#select-a-target-in-the-workbench}

在实验页的 **本页测量上下文** 中展开 **浏览样品、批次与记录集**。
**Registered target**（已登记目标）列表显示目录最新修订。
选择单成员目标，核对名称、修订、所属目录和精确样品成员。
多成员或含显式连接的目标会标为不支持执行，不能因注册成功就开始测量。

选择固定显示的修订。**Refresh target list**（刷新目标列表）、刷新作者源码和切换实验
都不会推进该选择。采用新版本必须显式选择并重新预览；
即使目录头已变化，重开保存计划仍保留原目标。外部目录引用会报错，不转成普通样品。

目标需与选中参数或保留配置证据兼容，预览检查精确样品与批次是否一致。
批次、记录集和操作者仍是各自独立选择；不能用新选择覆盖候选已有的科学上下文。
科学选择变化会使旧预览失效，采集前须重新检查。

工作台内切换页面时保留草稿；切换连接的数据目录会清除它。
这些变化不影响另一 Notebook 的选择，也不改变已接纳测量。

适配器开发者可用 `scopecat.config.target_projection.project_target()` 验证显式成员到运行时拓扑的映射。
它检测两个样品都声明 `q0` 等冲突，并对照所给 setup 拓扑检查样品内及样品间连接，
见[投影契约](../development/architecture/target-execution.md#explicit-multi-member-topology-checks)。
此检查不启用多成员执行，也不改变工作台的单成员接纳边界。

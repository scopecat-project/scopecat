# 在记录集中为运行编号 {#number-runs-within-a-record-collection}

记录集是持久保存的编号空间。例如，为一次降温创建记录集，在切换实验或源码文件后继续使用。
集合名称可以修改，但集合 ID 和每次已接纳运行的地址不变。研究课题可以关联多个记录集中的运行，
不会移动记录或重新编号。

在已配置项目的作者会话中使用（例如 `session = sc.notebook()`）；准备实验前，
先按[独立参数流程](independent-parameters.md)选好参数和 setup：

```python
collection = session.create_record_collection("Chip A · September cooldown")
prepared = session.prepare("signal", record_collection=collection.id)
run = prepared.run().wait().result()

session.history(collection=collection.id)
first = session.run(1, collection=collection.id)
address = session.get_run(first.id).address
```

集合只需创建一次。下次打开 Notebook 时，用 `session.record_collections()` 查找，
再用 `session.record_collection(collection_id)` 按精确 ID 重开。
再次调用 `create_record_collection` 会创建另一个集合，即使名称相同。
自动化需要自定稳定 ID 时，可使用
`session.save_record_collection("chip-a-cooldown-2026-09", RecordCollectionEdit(...))`；
`RecordCollectionEdit` 从 `scopecat.records.record_collection` 导入。
重命名沿用原 ID，并传入 `expected_revision=collection.revision`。
若另一客户端已修改元数据，先重新读取，再审查并提交修改。

集合选择属于已准备请求。两个 Notebook 可以选择不同集合，也可以共享同一集合而不产生重复编号。
已接受提交的重试保留原编号；沿用同一提交身份却改选集合会产生冲突。
编号在接纳时分配，因此排队、取消和失败的运行也保留编号；编号不是列表位置，也不是成功测量次数。

`session.prepare_plan(ref, actor="alice", record_collection=collection.id)` 为已保存实验计划的
这一次执行选择记录集。保存计划配方不会固定目标集合；预览和提交会固定选择，改选后需重新预览。

直接调用 `LabClient.run(..., record_collection=collection.id)` 或
`LabProcedureContext.run(..., record_collection=collection.id)` 也支持此选择。
自定义 launcher provider 必须显式把集合选择传入子请求；内置 authored launcher 会拒绝不支持的
维护方 provider，不会悄悄将运行放入默认集合。

## 已有 Notebook 与保留数据 {#existing-notebooks-and-retained-data}

三种运行身份承担不同职责：

| 身份 | 用途 |
| --- | --- |
| 集合 ID + 集合内编号 | 在指定记录集中精确寻址；命名集合从 1 独立分配编号 |
| 全局调度编号 | 数据空间内原有的数字寻址及分页；不是某个命名集合的局部号 |
| 完整 `run.id` | URL、机器引用和数据交换中的稳定运行身份 |

会话没有选中集合时，不带集合参数的 `session.run(number)`、`session.run_number(run)` 和
`session.history()` 使用原有全局调度编号。未指定集合的新运行进入默认集合，并沿用全局编号；
其他集合的运行会占用全局号，因此默认集合的编号可能不连续。
分享短地址时应同时保留集合 ID 和局部编号。名称只是标签，重名时不能据此确定唯一记录。

新运行的 `run.id` 使用完整、小写、带连字符的 UUIDv7，接纳和科学数据交换入口要求此规范格式。
URL、引用和导出数据应保留完整字符串，不解析其含义。人工查看时可显示集合名称＋局部编号，
但精确寻址使用集合 ID；运行时间以 `created_at` 为准，不按 UUID 排序。
不提供旧标识转换或自动数据迁移。

默认集合 ID 来自当前数据空间身份。[备份与恢复](backup-and-restore.md)保留这些地址。
历史数据能否打开或升级，以[数据兼容性策略](../development/data-compatibility.md)和
[应用维护](maintain-application.md)中的受支持路径为准；本页不增加旧格式导入或跨空间地址转换能力。

[Notebook 会话选择](select-session-context.md)可按客户端/内核记住集合，实验工作台则保留本页选择。
这些入口使用同一套存储与 Python/HTTP 契约，背景见
[实验工作台上下文](../development/architecture/experiment-contexts.md)。
没有跨数据空间目录。记录集本身不隔离硬件，也不复制参数；教程仍使用自己的隔离机制。

## 在应用中连续采集和找回结果

工作台常驻 **Record collection**（记录集）选择。集合只需创建一次，再选择 **Use 集合名称**。
通过 **返回准备** 返回，再选择 **准备另一次采集**，可沿用本页集合继续采集；
独立结果窗口不会改变采集页的选择。

在 **数据** 页选择记录集浏览记录。**所有记录集** 包含默认集合和命名集合，不等于默认集合本身。
先选择一个集合，再输入 **记录集内 run 编号** 并选择 **查找 run**，按集合短号精确找回。
这个查找不受下方课题、样品、时间和高级筛选影响，查无此号也不会转为查找全局号。
失败、取消的运行保留编号；导入结果仍在下方。

地址优先显示集合名称和集合内号。提示或展开的身份信息提供精确集合 ID、全局编号和稳定 run ID，
用于重名消歧。**Rename collection**（重命名集合）只改名称，课题关联也不移动或重新编号。
**复制 run 引用** 同时复制可读名称和精确身份；**复制只读代码** 使用稳定 run ID，
并附显式 `session.run(number, collection="collection-id")` 写法。
Notebook 须连接同一数据空间；其当前集合选择不会改变复制代码指向的记录。

工作点和部署的精确 ID 筛选位于 **高级来源筛选**。记录集仍是平面的编号空间，不提供层级目录或科学条件标签。

完整 run ID 可在 **数据 → 按完整 run ID 查找** 中粘贴，再选择 **查找精确 run**。
此入口与集合短号查找并列，只精确查询当前数据空间，不受列表分页或筛选影响。
ID 作为不透明字符串使用；不解析 UUID 版本，不支持前缀或跨空间搜索。
界面分别提示引用格式错误、当前空间不存在和服务不可用；连接中断时不会把缓存结果当作新查询成功。

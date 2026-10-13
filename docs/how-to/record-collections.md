# Number runs within a record collection

A record collection is a persistent numbering namespace. For example, create one
for a cooldown and keep using it when changing experiments or source files. Its
name can change; its ID and each admitted run's address cannot. A research project
can group runs from several collections without moving or renumbering them.

With an existing author session (`session = sc.notebook()` in a configured project):

```python
collection = session.create_record_collection("Chip A · September cooldown")
prepared = session.prepare("signal", record_collection=collection.id)
run = prepared.run().wait().result()

session.history(collection=collection.id)
first = session.run(1, collection=collection.id)
address = session.get_run(first.id).address
```

Create the collection once. On a later notebook visit, use
`session.record_collections()` to find it and
`session.record_collection(collection_id)` to reopen it. Calling
`create_record_collection` again creates a distinct collection even if the name
matches. For an automation-owned stable ID, use
`session.save_record_collection("chip-a-cooldown-2026-09", RecordCollectionEdit(...))`
with `RecordCollectionEdit` imported from `scopecat.records.record_collection`.
Rename with the same ID and `expected_revision=collection.revision`; refresh
metadata if another client has changed it.

Selection belongs to the prepared request. Two notebooks can select different
collections, or share one without duplicate numbers. An accepted submission keeps
its original number on retry; reusing that submission identity with a different
collection is a conflict. Numbers are assigned on admission, so queued, cancelled
and failed runs retain their numbers. Numbers are not list positions or counts of
successful measurements.

`session.prepare_plan(ref, actor="alice", record_collection=collection.id)` selects
the destination for one execution of a saved experiment plan. Saving the recipe
does not freeze its destination collection. Preview and submission do freeze the
selection; changing it requires a new preview.

Direct `LabClient.run(..., record_collection=collection.id)` and
`LabProcedureContext.run(..., record_collection=collection.id)` also support this
selection. Custom launcher providers must explicitly forward selection into their
child requests; the built-in authored launcher rejects an unsupported maintained
provider instead of silently placing runs in the default collection.

## Existing notebooks and retained data

Without a selected session collection, unqualified `session.run(number)`,
`session.run_number(run)` and `session.history()` continue using the store's existing global scheduler numbers. New runs without a
collection selection belong to the default collection and keep those same numbers.
The default collection can therefore have gaps when runs use other collections.
New named collections start at 1 and allocate independently. Use the collection ID
alongside its number when sharing a short address; retain `run.id` for existing
machine references. Collection names are labels, not identifiers.

新运行的 `run.id` 使用完整、小写、带连字符的 UUIDv7，接纳和科学数据交换入口要求此规范格式。
URL、引用和导出数据应保留完整字符串，不解析其含义。人工寻址使用集合名称＋局部编号；
SDK 全局数字寻址和分页仍使用调度序号，运行时间以 `created_at` 为准，不按 UUID 排序。
不提供旧标识转换或自动数据迁移。

The default collection ID derives from the current store identity.
[Current-format backup/restore](backup-and-restore.md) retains its addresses;
there is no supported import or upgrade of earlier development stores. Keep old
files separately under the [data policy](../development/data-compatibility.md).

This is the storage and Python/HTTP foundation for the
[experiment workbench context](../development/architecture/experiment-contexts.md).
[Notebook session selection](select-session-context.md) can remember a collection
per client/kernel, and the experiment workbench has page-local selection. There
is no cross-store catalog. A collection alone neither isolates hardware
nor copies working-point parameters; teaching still uses its existing isolation.

## 在应用中连续采集和找回结果

工作台常驻 **Record collection** 选择。集合只需创建一次，再选择 **Use**。
通过 **Return to preparation** 返回并选择 **Prepare another acquisition**，
可沿用本页集合继续采集；独立结果窗口不会改变采集页的选择。

在 **Data** 选择集合浏览记录。**All collections** 包含默认集合和命名集合，
不等于默认集合本身。输入 **Collection run number** 后选择 **Find run**，
按该集合的短号精确找回，不受下方课题、样品、时间和高级筛选影响；
查无此号不会转为查找全局号。失败、取消的记录保留编号，导入结果仍在下方。

地址优先显示集合名称和集合内号。提示或展开的身份信息提供精确集合 ID、
全局编号和稳定 run ID，用于重名消歧。**Rename collection** 只改名称，
课题关联也不移动或重编号。**Copy run reference** 同时复制可读名称和精确身份；
**Copy read-only code** 执行稳定 run ID，并附显式
`session.run(number, collection="collection-id")` 写法。
Notebook 须连接同一数据空间，其当前集合选择不会改变复制代码指向的记录。

工作点和部署的精确 ID 筛选位于 **Advanced source filters**。
集合仍是平面的编号空间；本轮不提供层级目录或科学条件标签。

完整 run ID 可在 **Data → Find by full run ID** 中粘贴，选择 **Find exact run**。
此入口与集合短号查找并列，只精确查询当前数据空间，不受列表分页或筛选影响。
ID 作为不透明字符串使用；不解析 UUID 版本，不支持前缀或跨空间搜索。
界面分别提示引用格式错误、当前空间不存在和服务不可用；连接中断时不会把缓存结果当作新查询成功。

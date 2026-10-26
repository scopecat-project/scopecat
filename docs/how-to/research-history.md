# 整理持续积累的研究历史 {#organize-continuous-research-history}

研究项目是持久数据空间内有名称的一组关联，独立于 Python 源码工作区、物理样品和当前参数选择。
多个研究项目可以关联同一个样品或运行（run），不会复制或移动测量数据。

在工作台打开 **数据（Data）→ 运行与记录**，按以下顺序整理关联：

1. 展开 **研究项目管理**。新建项目时，先在 **研究项目** 中选择 **All projects**，
   填写 **项目名称**，点击 **Create project**；已有项目则直接从 **研究项目** 中选择。
2. 在 **Sample** 筛选框选择样品，再在 **研究项目管理** 中点击 **关联样品**。
   样品关联表示参与关系，不会自动纳入该样品过去或将来的全部运行。
3. 勾选 **显示项目外的 run 以供关联**，检查运行列表，在需要纳入的运行旁点击 **关联 run**。
   该选项取消研究项目筛选，其他筛选条件仍生效；列表不保证只含尚未关联的运行。
4. 取消勾选后，列表只显示所选项目中符合其他筛选条件的运行。
   展开管理区时可点击 **移除 run 关联**；关闭管理区会隐藏关联按钮，但不会自动清除上述勾选状态。

可按样品、记录集或时间区间筛选。在 **高级来源筛选** 中填写精确的工作点上下文 ID 或实验台部署 ID；
这些标识不等于科学条件。截止时间不包含在区间内。界面显示本地时间，提交筛选时转换为 UTC 时间戳。
新接纳的运行保留接纳它的部署身份；缺少这项证据的旧数据保持未知，不会用当前实验台补作来源。

点击运行条目，进入运行详情查看原始测量、精确配置与源码，以及单独发布的分析。
修改项目名称或移除关联只改变组织方式，不会删除运行，也不会让另一实验台采用其中的参数值。
选中项目后，可在管理区修改名称并点击 **Rename project**。
重命名会检查所选修订；若其他会话已修改项目，应重新加载所选项目再操作。

通过作者会话进行 Python 自动化：

```python
from scopecat.records.research_project import ResearchProjectEdit, RunHistoryFilter

project = author.save_research_project(
    "resonator-study", ResearchProjectEdit(name="Resonator study")
)
author.associate_research_member(project.id, "samples", "chip-1")
author.associate_research_member(project.id, "runs", run_id)
page = author.list_runs(
    sample_id="chip-1",
    history=RunHistoryFilter(research_project=project.id, working_point="cooldown-1"),
)
```

项目、成员关联与运行查询都使用有界分页。继续查询时使用返回的游标：项目和运行为 `before`，
成员关联为 `after`。重命名使用 `ResearchProjectEdit(expected_revision=project.revision, name=...)`，
解除关联使用 `associate_research_member(..., present=False)`。
显示名称变化不会改变稳定 ID。

此功能使用当前开发格式，拒绝旧格式；详见[数据兼容性策略](../development/data-compatibility.md)。
请保留与数据匹配的读取器，并按[备份与恢复](backup-and-restore.md)保存恢复副本。
名称或列布局相同并不能证明不同运行在科学上可比较；仍需明确核对单位、方法、配置和条件。

若需独立于这些关联的采集编号，见[记录集](record-collections.md)。

# 整理持续研究历史 {#organize-continuous-research-history}

研究项目是持久数据空间内的命名关联，与 Python 源码工作区、物理样品和当前参数选择不同。
多个研究项目可以引用同一样品或 run，不复制或移动测量载荷。

在工作台打开 **数据 → 研究项目管理**。**研究项目** 选择“所有研究项目”时，输入名称并点击
**创建项目**；选中已有项目后，同一表单提供 **重命名项目**。
名称由用户决定，重命名不改变稳定 ID。

选择 **样品** 并点击 **关联样品**。样品成员关系只表示参与，不自动包含该样品过去或未来的 run。
勾选 **显示项目外的 run 以供关联**，审阅列表后点击 **关联 run**；取消勾选后只查看项目已关联的 run。
该勾选仅放宽研究项目筛选，样品、记录集、时间及高级来源筛选仍生效。
**移除样品关联** 和 **移除 run 关联** 只改变组织关系，不删除样品或测量。

可以按样品、记录集或时间区间筛选；**高级来源筛选** 接受精确工作点上下文 ID 和实验台部署 ID，
这些标识本身不描述科学条件。截止时间不包含在区间内。
界面显示并接收本地时间，查询转换为带时区的时间戳。
新接纳运行保留接纳它的部署身份；旧数据缺少这项证据时仍为未知，不把当前实验台补写为来源。

打开 run 可检查原始测量、精确配置与源码，以及单独发布的分析。
研究项目重命名或解除关联都不会让另一实验台采用其参数。
并发重命名按选中修订校验；发生 **项目更新冲突** 时刷新页面并重新选择，核对最新内容后再保存。
错误提示保留 **原始诊断**；写入响应未知时先核实已有项目或关联，不把报错当作一定未写入。

作者会话中的 Python 自动化使用相同接口：

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

项目、成员和运行查询均分页返回。继续查询时使用返回游标：项目/run 使用 `before`，成员使用 `after`。
重命名用 `ResearchProjectEdit(expected_revision=project.revision, name=...)`，
解除关联用 `associate_research_member(..., present=False)`。显示名称变化不改变稳定 ID。

当前数据格式、历史格式读取和升级范围以[数据兼容性策略](../development/data-compatibility.md)为准；
保留匹配读取器，并遵循[备份与恢复](backup-and-restore.md)。
相同名称或列结构不证明不同运行在科学上可比较；单位、方法、配置与条件仍需明确审阅。

独立于项目关联的采集编号见[记录集合](record-collections.md)。

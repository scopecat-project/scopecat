# Compare retained runs without acquisition

Use **Compare retained runs** in Analyses, run detail or procedure progress.
Choose two completed retained runs, inspect their compatible coordinate and
observable, select point positions independently and run a laboratory-owned
Python model. This operation never dispatches a procedure or starts acquisition.

The device-free integration fixture registers `signal-quadratic` through
`testing/fixtures/retained-signal/src/ui_signal/comparison.py`; its editable model lives in
`testing/fixtures/retained-signal/src/ui_signal/model.py`.
Acquire two `ui_signal.signal` runs in that project over several frequencies,
then compare them. The quadratic model exposes an adjustable carrier offset. It
is a synthetic integration example, not physical calibration evidence. Experiment
authors can edit the authored fit/helper and its model version, then select
**Refresh author code**. Registering or replacing the project-level
`LabApplication(comparison_provider=...)` callback is a maintainer task, using the
public data types in `scopecat.records.comparison` and provider/handoff types in
`scopecat.application.comparison`.

When a provider suggests a follow-up experiment, use
`comparison_selection(primary.snapshot)` from that module as the new
`LaunchRequest.selection`. It retains exact parameter/setup or candidate inputs,
subject revision and batch instead of resolving today's global defaults. The
fixture provider uses this path, including in equipment-only projects without
a parameter default. The suggestion still needs a new checked preview and normal
execution admission; retaining inputs does not approve a candidate or permit
execution under obsolete equipment authority.

The optional `scopecat.api.comparison.comparison_inputs` helper checks one
real scalar coordinate and observable per point. It rejects arrays, unavailable
or nonfinite values, incompatible units, stale hashes and unfinished inputs.
Different grids remain separate: no interpolation or cross-run join is performed.
This helper uses existing Dataset reads, not storage pushdown, and supports up to
10,000 points per retained run. It is not a process-memory budget.

The fixture fit traces the original immutable datasets and exact ordered
selection positions as explicit bindings. Its publications retain both run IDs
and measurement hashes, model ID/version, parameters, coefficients and a fitted-curve figure. A fit is a normal primary-run analysis revision.
The primary run must actually be an input; secondary measurements are checked
against their true owner and content hash, and secondary runs must be complete.
Project analyses still cannot publish parameter proposals. Nothing copies an
analysis into another owner to bypass those checks.

Reopen old and new results in **Primary run analysis history**. Reading a result
does not execute a model. Providers publish via
`scopecat.api.comparison.save_comparison(analysis, request)`, which adds the
public typed `comparison-request` fact to the existing atomic analysis save.
Source actions validate the exact run, analysis and publication hash before
loading project code. They retain the original model, parameters and selections;
editing the current form cannot change what an old candidate or review means.
Configure `[authors].source_roots` to include the editable fit/helper module.
**Refresh author code** validates and retains the source revision before another
inspection. Inspection returns its actual `AuthorRevisionRef`; fitting requires
that reference, which is saved in the request fact. Reopening a source action
loads its retained revision before importing the application, even after active
code changes. The existing revision manifest checks the required external Python
and package environment; it does not archive installed dependencies. Comparison
execution without a retained author revision is rejected.

**Create explicit candidate** publishes a separate analysis referencing the exact
saved fit and using the primary run's frozen configuration as its proposal base.
It does not activate a configuration or reopen a closed procedure. **Record
candidate rejection** saves an independent typed fact with actor, reason,
proposal IDs and the candidate analysis ID/publication hash. This is neither
procedure approval nor global deletion of the candidate. Configuration
acceptance, default activation and calibration validity remain separate acts.

## 从结论直接启动下一实验

在保存的结论中选择 **Import suggested inputs into Launch**，审阅配置并按需编辑建议，
然后直接 **Start acquisition**。无需先命名保存 plan，也无需单独点击 Preview；
Start 沿用现有准备检查，不跳过配置或接纳校验。

接纳时固定原运行 ID、analysis publication ID 和 publication hash，作为独立的
`decision_source`；实际提交的控制、输入和配置另行保留。修改建议不会把来源换成新修订，
该关系也不表示分析读取了后续运行，不进入直接数据输入索引。

新运行的 **Open exact source conclusion** 打开原结论的精确版本；结论中的
**Experiments started from this conclusion** 分页列出由它启动的运行。来源只在当前
数据空间解析，错误 hash 或另一空间的记录无法作为新接纳的来源。

草稿中的编辑及普通页面往返保留来源；重置、切换实验或显式导入另一组输入沿用草稿替换规则。
已经提交的来源不随草稿清除而改变。响应丢失时用 **Check original submission**
恢复原任务，不重新开始采集。重启及当前格式备份恢复保留关联；科学数据导出携带精确
来源证据，导入的 capture 仍是只读证据，不创建可执行任务。

也可将建议保存为 [named experiment plan](save-experiment-plans.md)，其来源和计划引用
继续沿已有路径固定。来源说明的是发起依据，不代表建议已经采纳为默认配置或取得设备执行资格。

## 找回记录并继续比较

Data 的集合筛选和短号查找位于紧凑工具栏；查号不受课题、样品或时间筛选限制。
比较选项与图例显示集合名和集合内号，重名时附集合 ID，缺失名称不会伪造。
代码工作区、刷新操作和技术身份位于 **Analysis code and source details**。

同一窗口的 Back/Forward 保留当前比较对象、选点和输入；状态按数据空间隔离。
刷新后恢复输入，但须显式 **Inspect compatible data** 后才能拟合，不会自动重跑分析。
已保存分析仍从历史读取。未保存输入属于当前浏览历史条目，不是跨窗口共享草稿。

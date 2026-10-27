# 将已验证候选发布到参数分支 {#publish-a-verified-candidate-to-a-parameter-branch}

参数分支保存命名的科学输入历史，样品、实验批次和 setup 分别选择。
[普通参数保存](parameter-branches.md)只保存编辑，不能代替候选验证。
发布只推进已审阅分支，不改变其他分支、会话选择或设备配置；
分支身份和名称都不证明科学有效性。

## 在采集前固定基线

先保存希望纳入基线的试调值，再捕获目标分支头。
以下 `session` 是已连接的作者会话，`experiment` 是实验请求，分支和 setup 已存在。
按实验需要先选样品/目标和批次；示例不推断这些科学身份：

```python
baseline = session.parameters.checkout("chip-a/daily").head
setup = session.setup.get("bench-v1")
session.use(parameters=baseline.revision, setup=setup)
baseline_run = session.prepare(experiment).run().wait().result()
```

最后一行显式执行采集。保存 `baseline` 的精确分支名、generation 与参数修订，
不要在发布前用最新检出覆盖它。未保存覆盖值不等于保存基线；
若准备带有这类覆盖，不能把它当作原分支的基线来发布。

## 从候选验证到发布

按[候选验证流程](verify-parameter-candidates.md)，从 `baseline_run` 的保留分析暂存候选，
用该精确候选采集独立新数据，再执行实验室的验证策略并保存决定。
只有拟合成功、旧数据重分析或采集成功都不够。下例 `verified` 是这个候选取得的正向保留验证结果：

```python
published = verified.publish_to_branch(
    baseline,
    name="chip-a-calibrated-002",
    note="独立验证后接纳",
)
# 发布不会自动移动当前会话选择；显式采用回执中的精确修订。
session.use(parameters=published.revision, setup=setup)
```

服务端核对精确基线、提议、验证和目标分支 generation，不把编辑过的 Python 结果视为验证证据。
候选范围包含其保留的科学对象、批次和 setup/执行场景；
接受只覆盖被提议的单元格与记录的科学上下文，不覆盖整个分支或另一样品。

校准期间分支若已推进，新的发布请求会冲突，不静默合并。
先审阅新 head；若基线参数已变，应基于新修订重新采集证据，
而不是给旧候选换上新 generation。普通保存与已验证发布的区别及组合候选要求，见
[分支发布规则](parameter-branches.md#publish-a-verified-candidate)。

响应丢失时，重复完全相同的发布调用：保留原候选和验证、`baseline`、结果名称、操作者及 note。
结果修订、接受证据和分支推进一起提交；同一请求可恢复历史回执，即使之后 head 又已变化。
不要改名称或重新选择 head 来“重试”。分支回执保留生产运行与验证引用。
自动化流程应把目标与结果名称冻结在 intent 中，见
[持久 procedure 发布](parameter-branches.md#publish-inside-a-durable-procedure)。

## 从工作台接纳 {#adopt-from-the-workbench}

在**数据**中打开候选所在的精确运行，展开候选的接纳与记录审阅入口
（当前按钮 **Review adoption and records**）。选择目标分支，
比较其当前值与候选的原值、建议值，再选择合格的保留独立验证。
审阅时固定的分支修订和 generation 不随刷新移动；刷新证据不 rebase 候选，分支变化会阻止接纳。

点击 **Adopt verified candidate into〈分支名〉** 才显式推进该分支。
对应工作表仍完整保留，包括非法原始编辑；保存工作表检查点前，
先在**配置**中审阅已改变的分支。其他分支的工作表不阻止接纳。
界面分开显示已收到的工作输入和将发布的值，未确认接收的编辑不在该快照内。

没有合格验证时，保留候选待验证，并按[作者验证流程](verify-parameter-candidates.md)取得证据。
**Load older verification records** 显式加载更早记录；
旧数据重分析、拒绝决定或另一候选的验证不能授权本次发布。
当前接纳入口仍显示上述英文按钮，不能将它与普通“保存”操作混同。

响应丢失或无法读取时，**Recover original adoption** 重复保留的原请求。
浏览器标签页在重载后仍保留该请求，并绑定应用数据身份与精确候选；
另一数据空间或重置后的空间不能重放它。
选中分支的发布历史是持久接纳记录，可在此重开查看接受修订、操作者、说明和精确验证。

持久数据的读取、备份与支持升级范围以[数据兼容策略](../development/data-compatibility.md#what-remains-supported-now)
为准；发布本身不新增兼容基线或迁移路径。

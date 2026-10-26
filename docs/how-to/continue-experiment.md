# 同一个实验，隔次打开继续做

重新打开原来的 Scopecat 数据空间，从 **数据（Data）→ 运行与记录** 找回结果，
再决定是否准备下一次实验。重看历史不需要手工填写两个 run ID，也不需要
`continuation.json`。这些记录不会恢复 kernel、任意 Python 对象或执行许可。

本页的续采代码使用[独立作者目录的合成信号示例](../tutorials/starter-authoring.md)：
在**设置 → 作者代码 → 新建代码目录**创建目录，打开 `notebooks/02_edit_scan.py`，
选择该目录的 `.venv`。它与[首次实验课程](../getting-started/quickstart.md)的
`notebooks/parameters.ipynb` 使用不同文件，不要把两套单元格混用。均不需要真实设备。
框架开发者使用[源码开发入口](../development/public-preview.md#daily-source-desktop)，
隔次重开沿用同一 `--home` 路径。

## 先找回准确结果，只读重看

1. 打开原应用和已登记的作者目录，选择原作者 Python。内核重启后先执行 Imports、Connect；
   应用已重启而旧会话仍在时，先关闭旧会话，再重新连接。核对 Connect 显示的数据空间与作者目录。
   只为读取已有结果，不必执行 Preview、Submit 或 Run All。
2. 在 **Data → 运行与记录** 筛选记录集、样品或时间，打开目标运行。
   两次运行可能同名、参数也相近；在运行详情核对记录集 ID/本地编号、稳定 run ID、
   时间及保存的输入，不按显示名称或“最近一次”猜选。已有完整 ID 时，可用
   **按完整 run ID 查找 → 查找精确 run**；此入口仅查当前数据空间。
3. 若要重看结论，在该运行的分析列表打开目标**已保存分析**，核对发布 ID、版本及“来源与执行证据”中的输入。
   执行复制的代码后，可用 `publication.publication_hash` 核对准确内容 hash。相同 analysis key 可以有多个 publication；
   选择当时那一个，而不是让 key 解析到最新版本。运行详情与具体分析各自提供
   **复制只读代码**：前者找回 run，后者同时找回该 run 的精确 publication。
4. 将代码粘贴到独立单元格，只运行该格。生成代码使用 `session`；本 starter 的
   Connect 使用 `author`，先执行 `session = author` 即可复用同一连接，不另开数据空间。
   剪贴板不可用时，界面提供可选择的同一段只读代码。

例如，界面生成的调用形如 `run = session.run("稳定 run ID")`，以及
`publication = run.published_analysis("准确 publication ID")`；实际复制的代码
已填入所选记录的 ID，不需要照着这个示意手工输入。只读代码不重建自定义 typed/group 对象。
若需要比较两个相似运行，分别从各自详情复制代码，读取后依次保留 `first = run` 和
`second = run`，不要让第二段代码覆盖后丢失第一个引用。

缺失记录或数据空间不匹配时，核对原空间和精确引用，不替换为第一条记录、最新运行或
最新分析。复制并执行读取代码不会新采集，也不会重新执行分析。

## 选择结论，再显式准备下一次实验

重读、新分析与新采集是三个动作：

| 要做的事 | 操作与结果 |
| --- | --- |
| 重看已保存结果 | 执行复制的只读代码；不创建运行或 publication |
| 产生新结论 | 明确执行分析并保存新 publication；不改写先前 publication，不自动采集 |
| 继续采集 | 重新构造请求、显式 `prepare`，再单独 `.run()`；生成新运行 |

假设已从**所属 run 的具体分析**复制并执行代码，得到 `run` 和 `publication`，
且这份结论包含本合成示例的 `suggested_scale` fact。先固定决策来源：

```python
from scopecat.records.plan_ref import PlanAnalysisSource

conclusion = publication
source_ref = PlanAnalysisSource(
    run_id=run.id,
    analysis_id=conclusion.id,
    publication_hash=conclusion.publication_hash,
)
```

`decision_source` 需要 run 所属的准确分析引用；不要把 project 分析或另一个同名 run
填入 `run_id`。这个三元组保留“为什么选择下一步”，不等于下一次实验的实际输入。

确认要采集后，执行 starter 的 Preview 单元格，刷新已保存源码、重新导入声明、
打开参数并构建新请求；该格名称不表示必须查看预览，`print(prepared.preview)` 可省略。
再按此次意图修改输入并重新准备：

```python
# 以下操作显式保存参数并准备新工作，不属于只读回看。
params["response"]["signal"]["scale"] = conclusion.fact("suggested_scale").value
params.save(note="采用所选精确结论中的参数")
request.values["center"] = 0.5
prepared = author.prepare(request, parameters=params, decision_source=source_ref)
# 可选：print(prepared.preview)
```

`prepare` 冻结此次输入并执行已有校验；随后再改请求或参数，需要重新 prepare。
只有明确执行以下采集单元格才开始新运行：

```python
job = prepared.run()
next_run = job.wait(timeout=120).result()
print(next_run.id)
```

等待超时继续等待同一个 `job`；提交结果不确定时按原 receipt 找回，不重复 Submit
或 `.run()`。新运行保留原 `decision_source`，后续参数修改或同 key 的新 publication
不替换旧引用。来源必须在当前数据空间可解析且 hash 一致。下一次运行的
**Decision source** 可打开精确结论；结论下的后续运行只是决策关联，不是该分析的数据输入。

## 可选：创建一份新的比较结论

尚无要采用的结论时，可以对前面明确选中的 `first`、`second` 执行一次新分析。
本例的 `0.75` 是作者选择的下一步参数，不是自动接受的校准建议：

```python
comparison = second.analysis("比较响应幅度", key="scale-comparison")
left = comparison.measurements(run=first, id="before")["result"].require_values()
right = comparison.measurements(id="after")["result"].require_values()
conclusion = (
    comparison.result()
    .fact("peak_ratio", max(right) / max(left))
    .fact("suggested_scale", 0.75)
    .save()
)
source_ref = PlanAnalysisSource(
    run_id=second.id,
    analysis_id=conclusion.id,
    publication_hash=conclusion.publication_hash,
)
```

这里的 `.save()` 发布新分析，不能当作只读恢复单元格反复执行。
修改 `src/scopecat_lab/authored/signal.py` 后，旧运行和旧结论仍保留原数值与源码；
要使用新源码重新分析，应遵循[起步实验的显式分析步骤](../tutorials/starter-authoring.md#edit-code-and-retain-evidence)。

## 退出、重开与可选书签

保存作者文件，关闭 kernel，选择**退出 Scopecat**；只关闭窗口可能留在托盘。
重开同一应用数据空间及作者目录后，重新连接，再走 Data 的只读路径。
不要为了找回历史运行而执行 Submit，也不要把重新连接理解为自动恢复上次执行。

熟悉 Python 的作者也可手动使用两个稳定 run ID，或保存普通笔记：

```python
import json

(project.root / "continuation.json").write_text(
    json.dumps({"decision_source": source_ref.model_dump(mode="json")})
)
# 下次连接原数据空间后，仅精确读取，不创建新分析或运行。
notes = json.loads((project.root / "continuation.json").read_text())
source_ref = PlanAnalysisSource.model_validate(notes["decision_source"])
conclusion = author.run(source_ref.run_id).published_analysis(source_ref.analysis_id)
assert conclusion.publication_hash == source_ref.publication_hash
```

此文件只是可选引用书签，不是通用 continuation 对象、session 快照、恢复协议或必需格式。

## 验证范围

软件验证覆盖真实后端、独立 Python kernel 和浏览器中的准确运行/旧 publication 回读，
并核对读取前后的运行与 publication 数不变。它不能替代原生宿主、编辑器或硬件观察。

[#1034 的原生证据](https://github.com/scopecat-project/scopecat/issues/1034)仍只属于其原提交与范围：
Windows 完整 A/B 观察在 `d290e18acf7f7ed2967e58cefb97e2024d603401` / Python 3.14.7；
整合候选 `c9086685845b015aa19f9af55ef3903725eb3976` / Python 3.15.0 的 Mac 开发宿主复验
包含同 home 重开与显式续采，Windows 复验包含环境准备、历史读取及退出、未新增采集。
这些历史结果不构成最新 main 的原生保存/退出、完整 VS Code 交互或通用平台资格。
本指南更新不要求重跑原生矩阵。

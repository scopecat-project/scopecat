# 同一个实验，隔次打开继续做

面向熟悉 Python 的作者，使用 [首次实验](../getting-started/quickstart.md) 创建的合成信号目录；
不连接真实设备。开发者从公库运行 `uv run --locked python -m scripts.dev`，
需要隔离试用时使用该入口的 `--home /absolute/path/to/dev-trial`，下次沿用同一路径。

这是熟悉 Python 作者可执行的一条路径：下面仍需手工填写两个 run ID，并编写
`continuation.json` 笔记。它验证跨次继续的能力，不代表完整桌面体验已消除这些操作成本；
该笔记文件也不是所有实验必须采用的格式。

## 修改、运行、比较后继续

1. 在 Settings 创建作者目录，选其 Python 打开 `notebooks/02_edit_scan.py`，逐格完成首次运行。
   记下 run ID；**Submit** 才采集，重看结果不要 Run All。
2. 保存 `src/scopecat_lab/authored/signal.py` 的源码修改，例如将响应改为 `2 * scale / ...`。
   在执行 Close 前重跑名为 Preview 的 cell，刷新源码并重建请求和准备对象，再按需 Submit。
   该 cell 的名称不表示必须显示或查看预览；其中 `print(prepared.preview)` 可省略。
   已有记录仍保留原来的数值与源码。
3. 在同一 kernel 比较两次结果。若已 Close，先重新执行 Imports、Connect、Preview。
   把下面两个 ID 换成上述保存的记录；它们不会因后续编辑而变化。

```python
import json
from scopecat.records.plan_ref import PlanAnalysisSource

first = author.run("第一次的 run ID")
second = author.run("第二次的 run ID")
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
# 普通作者笔记，用于下次准确找回，不是应用迁移或自动恢复协议。
(project.root / "continuation.json").write_text(
    json.dumps(
        {
            "first": first.id,
            "second": second.id,
            "decision_source": source_ref.model_dump(mode="json"),
        }
    )
)
```

`0.75` 是本合成例子中作者选择的下一步参数，不是自动接受的校准建议。
在 Data/Runs 打开第二次记录即可看到保存的结论；比较记录明确引用两次输入。
确认想继续后，用已有准备入口保留决策来源：

```python
params["response"]["signal"]["scale"] = conclusion.fact("suggested_scale").value
params.save(note="采用本次比较选择")
prepared = author.prepare(request, parameters=params, decision_source=source_ref)
# 可选：print(prepared.preview)
# 单独执行下一行才创建新运行。
next_run = prepared.run().wait(timeout=120).result()
print(next_run.id)
author.close()
```

准备对象仍执行已有输入校验；显示或查看预览不是运行前置。
不需要分格操作时，可直接使用 `author.prepare(..., decision_source=source_ref).run()`。

决策来源与实际输入分别保留。后续修改参数不替换结论，新修订也不改旧引用。
`decision_source` 必须属于当前数据空间且 hash 一致。下一次运行的 **Decision source**
可打开精确结论，结论下可找回后续运行；这些后续运行不是该比较的数据输入。

## 停止应用和 kernel 后

保存作者文件及笔记，关闭 kernel，在 Scopecat 选择 **Quit**（只关闭窗口可能留在托盘）。
重新打开同一应用数据空间和作者目录，选原 Python，运行 Imports、Connect；
仅找回旧结果时不要执行 Submit：

```python
import json
from scopecat.records.plan_ref import PlanAnalysisSource

notes = json.loads((project.root / "continuation.json").read_text())
source_ref = PlanAnalysisSource.model_validate(notes["decision_source"])
old = author.run(notes["first"])
print(old.measurements()["result"].require_values())
conclusion = author.run(notes["second"]).published_analysis(source_ref.analysis_id)
assert conclusion.publication_hash == source_ref.publication_hash
```

要继续采集，再执行此例的 Preview cell 以刷新已保存源码、重新打开参数和构建请求；
随后修改 `request.values["center"] = 0.5`，使用上面的 `prepare(..., decision_source=source_ref)`，
按需查看预览或直接 `.run()`。旧结果保持原值，保存的参数选择仍可读取。
等待超时只等待原 job；提交不确定时按原 receipt 找回，不重复 Submit。

此路径的云验证使用真实独立 Python kernel、应用后端和 Chromium。
原生窗口 Quit/重开、VS Code 激活与 Python 选择仍需本地观察，不由浏览器验证代替。

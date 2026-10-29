# 编辑实验参数 {#edit-parameters-for-an-experiment}

在工作台的**配置**页，或 VS Code 中通过 `session.parameters` 编辑参数。
不必先选择样品或连接设备。保存数值本身不证明校准有效。

日常流程是：打开工作参数表，编辑并确认草稿已保存；将工作输入用于下次实验，
重新预览后再运行。需要固定、复用或分享这批值时，再明确保存参数版本。
[分享与导入配置](share-configuration.md)读取的是已保存版本，不是编辑器中的草稿。

## 在工作台中编辑 {#in-the-workbench}

已有日常参数表时，选择**工作参数分支**，点击**打开工作参数表**。
重新打开会恢复该分支的活动草稿，包括 `-`、`1e` 等尚未完成的文本。
也可以选择**已保存的参数版本**，点击**编辑副本**；**编辑另一份副本**会开始独立编辑。

数值、单位和备注会自动保存到应用数据。看到**草稿已保存到应用数据**后再退出；
尚未得到保存确认的输入，在强制终止后不保证恢复。关闭编辑器只是隐藏编辑界面，
切换页面、重开应用或重启服务可以恢复已确认的草稿输入。
空的数字文本仍是未完成输入；若要明确清空一个值，点击**标记为未知**。零是已知值。

关闭编辑器或切换所选参数版本时会等待草稿保存。失败时保留编辑器与错误，
可点击**重试保存草稿**；服务断连时输入仍留在界面中。普通浏览器关闭或刷新时，
未保存输入会请求离开警告；留在页面可继续重试，仍选择离开可能丢失未确认输入。
这项浏览器提示不能当作原生应用 Quit 的独立验收结论。

准备好后，点击**将工作输入用于下次实验**。这会校验并捕获当前输入，
但不创建固定参数版本，也不移动参数分支。选择实验所用的 setup，再预览和运行。
工作表后来发生变化时，需再次明确采用当前工作输入并重新预览；已提交实验保留原先捕获的精确数值。

若要保存固定版本，展开**保存参数检查点**，填写**新版本名称**，点击**保存参数版本**。
可以单独保存版本，也可明确保存到命名分支。成功后草稿结束并保留历史；失败时草稿继续保留。
工作分支的草稿结束后，再打开该分支会从其已保存的最新版本开始。
移除整个已知参数需要保存检查点，因为实验覆盖值不能表示这种移除。

并发写入时，冲突输入会保留供核对。如果分支已有新版本，先**核对分支最新版本**，
再决定是否**核对分支后保留我的表格**；这会保留整张工作表，不会自动逐格合并。
草稿历史可用于查看和复制较早输入；明确**丢弃草稿**会结束该草稿，但保留历史。

在已保存版本上点击**用于下次实验**，选择的是这个精确版本，而不是会随分支移动的引用。
设备连接地址应在**设备与驱动**中编辑，不属于参数表。

## 在 VS Code 中编辑 {#in-vs-code}

先从已有参数修订创建一次命名分支：

```python
initial = session.parameters.get("initial-estimates")
session.parameters.create_branch("chip-a/daily", revision=initial)
session.use(parameter_branch="chip-a/daily")
params = session.params
```

打开分支时，编辑器捕获该分支当前版本，然后可以编辑并查看差异：

```python
params["drive"]["q0"]["frequency"] = 5.1
print(params.diff())
version = params.save(note="Frequency estimate from the latest scan")
```

表名和字段名由作者声明。Quantity 字段接受带单位的值，例如 `sc.Quantity(5.1, "GHz")`。
通过兼容单位视图读取数值不会改写其存储表示。`None` 将非键单元格标记为未知，不会填入默认值。

`params.discard()` 丢弃本地编辑；`params.copy()` 创建独立草稿。
`params.save("chip-a/trial")` 保存到新分支并在这个编辑器中选中它；原分支和其他会话的选择不变。
这里的 Python 本地编辑不要与工作台自动保存的工作草稿混为一谈。

声明类型化字段或新表，见[参数声明](declare-dynamic-parameters.md)；
通过文件或 pandas 编辑，见[参数表交换](exchange-parameter-tables.md)。

## 准备并运行 {#prepare-and-run}

```python
setup = session.setup.get("bench-v1")
session.use(parameter_branch="chip-a/daily", setup=setup)
prepared = session.prepare(experiment, parameters=session.params)
print(prepared.preview)
run = prepared.run()
```

准备时会捕获精确的 setup、设备连接和参数输入。另一个页面更改选择不会改写这个请求。
修改设备连接需要重新解析 setup 并预览；已提交的任务应先完成或取消，再进行这类维护。

预览检查输入能否用于这个实验，不是独立的校准验证。
需要发布经过验证的候选时，使用[参数分支工作流](publish-working-point-calibration.md)。

## 冲突与历史 {#conflicts-and-history}

如果保存提示分支已变化，本地草稿仍保留。Python 编辑器的 `params.rebase()`
可以合并互不冲突的单元格变化；同一单元格冲突或表声明变化需要核对，不会静默替你选值。
这与工作台“保留我的表格”的整表选择不同。

`session.parameters.history("chip-a/daily")` 列出精确修订；
`session.parameters.get(version.id)` 打开指定版本，不移动任何分支。
保存、复制或恢复参数值，都不会把校准资格转移到新的样品、批次或 setup。

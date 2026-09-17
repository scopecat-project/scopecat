# 第二批小范围体验：修改、刷新、读取

这两项可以分开做，每项约 15–25 分钟。先做 A；另一天再做 B 也可以。
不需要 AI 助手；构建/安装由维护者先准备，不把重做安装或整套课程列为体验题，也不测试设备或科学结论。
请使用维护者提供的新版本建立**新的练习项目**，不要覆盖原来的代码或实验记录。
开始前由维护者确认这份指南与安装包属于同一次交付；旧安装不会因网页文档更新而升级。

**已经完成 private `358aa64` A/B 的使用者**：无需重新证明原来的标准流程。
在新版独立练习项目中，每次只选下面一个变化：

- 先运行连接单元，再改一个默认值，重复相同的 refresh/import 单元；看首次连接和编辑后
  是否还需要判断 load 与 refresh。参考 A，约 5–10 分钟。
- 对照 B，把平均函数和读类型共用的注解写成 `IQ`；看是否能从 `result_types.py` 理解
  单位与原生类型。只关注声明和读回，不重复整套拟合/发布题。
- 直接做文末“新增实验”，复制文件后使用同样的 refresh/import；它可以先于平均 IQ 进行。
  若保留逐 shot 返回，按原来的数组方式读取，只有完成 B 后才使用 MeanRow。

以下操作在 VS Code 的项目窗口和 Notebook 完成。路径均相对于打开的项目文件夹；
代码中的 `project`、`session`、`params`、`teaching_rabi` 来自 `start.ipynb` 的连接单元。
使用项目自己的 kernel，运行“启动实验服务”任务，然后只运行连接与参数读取单元即可。
可以在 Notebook 末尾增加本次练习单元；不用运行原课程的拟合、候选发布和验证题。

## A．修改默认值，只做一次刷新

目标：区分修改源文件、构造请求、预览和真正运行；旧请求不会自动变成新请求。

### 1．留下一次修改前的结果

```python
request_a = teaching_rabi()  # 此处故意不传 seed，让声明默认值生效
request_a.values["amplitude"] = sc.Scan(np.linspace(0, 0.8, 7))
print("当前 seed：", request_a.values["seed"])
prepared_a = session.prepare(request_a, parameters=params)
print("预览点数：", prepared_a.preview.point_count)
```

预期是 seed `200`、7 个点；这一步没有采集。确认后在下一格运行：

```python
job_a = prepared_a.run()
run_a = job_a.wait(timeout=120).result()
print("修改前 run：", run_a.id)
```

### 2．改一处源码，再重新创建请求

在 VS Code 打开 `src/my_experiment/teaching.py`，将声明中的 `seed: int = 200`
改为 `seed: int = 400`，保存。然后在 Notebook 执行：

```python
session.refresh()
from my_experiment.teaching import teaching_rabi

request_b = teaching_rabi()
request_b.values["amplitude"] = sc.Scan(np.linspace(0, 0.8, 7))
print("旧请求、新请求：", request_a.values["seed"], request_b.values["seed"])
```

预期为 `200 400`。不需要 `importlib.reload`。如果仍为 `200 200`，先记录实际代码与
输出，不要反复重启来掩盖问题；检查是否保存、是否在同一个项目内编辑、是否仍显式传入
`seed=200`。上面的 refresh 同步源码，随后的 import 更新 Notebook 中的变量。

重新预览并运行 B：

```python
prepared_b = session.prepare(request_b, parameters=params)
job_b = prepared_b.run()
run_b = job_b.wait(timeout=120).result()
print("修改后 run：", run_b.id)
```

到这里可以停止。先保存身份清单，再保存 Notebook；重开不需要重复采集：

```python
import json

(project.root / "editing-runs.json").write_text(
    json.dumps({"before": run_a.id, "shots": run_b.id}), encoding="utf-8"
)
```

记录：哪一步最不直观？是否能预测旧请求保留 200？是否需要查额外文档？

## B．返回平均 IQ，并用类型明确的结构读取

目标：只改变每个扫描点内部的计算，不把多个扫描点混成一个平均数。
这项可以接着 A 做。开始时 `teaching.py` 仍返回 64 个 shot。若重启过 kernel，先运行
连接单元；然后从清单取回两批结果：

```python
import json

ids = json.loads((project.root / "editing-runs.json").read_text(encoding="utf-8"))
run_a = session.run(ids["before"])
run_b = session.run(ids["shots"])
```

### 1．修改三个位置

在 `teaching.py` 的导入区添加：

```python
import numpy as np
from numpy.typing import NDArray
from my_experiment.result_types import IQ
```

在实验定义前添加普通平均计算：

```python
@sc.compute
def mean_iq(iq: NDArray[np.complex128]) -> IQ:
    return complex(iq.mean())
```

在 `TeachingData` 中把 `iq: sc.ProductRef` 改为 `iq: sc.DataRef[complex]`。
把实验最后的返回语句改成：

```python
return TeachingData(experiment.coordinate(amplitude), mean_iq(cast(sc.ProductRef, iq)))
```

这里沿用模板已有的原始采集 `cast`；无需另写 `output_type` 参数或新增 cast。
现阶段显式底层采集的类型接线仍是框架改进项，不要求通过背诵这行来证明理解。
`IQ` 在 `src/my_experiment/result_types.py` 只声明一次：

```python
type IQ = Annotated[complex, sc.Unit("ratio")]
```

计算与读取共用这个别名；`complex` 已说明复数标量，`Unit` 只补单位。这里保留 `ratio`，
平均后仍是复数。共享类型别名放在独立文件，不需要导入当前实验才能读取历史数据。

### 2．刷新并运行，读取原生 dataclass

保存源码，在 Notebook 新增：

```python
session.refresh()
from my_experiment.teaching import teaching_rabi

request_mean = teaching_rabi()
request_mean.values["amplitude"] = sc.Scan(np.linspace(0, 0.8, 7))
prepared_mean = session.prepare(request_mean, parameters=params)
job_mean = prepared_mean.run()
run_mean = job_mean.wait(timeout=120).result()
```

读取类型由你在 Notebook 明确选择，不需要重建实验或访问内部引用：

```python
from dataclasses import dataclass
from my_experiment.result_types import IQ


@dataclass(frozen=True)
class MeanRow:
    amplitude: sc.Quantity
    iq: IQ


rows = run_mean.result().rows_as(MeanRow)
print(rows[0].amplitude, rows[0].iq.real, rows[0].iq.imag)
```

预期 7 行，每行一个复数，幅度仍带 `arb` 单位。用原始 shot 对照：

```python
shots_b = np.asarray(run_b.measurements()["iq"].require_values())
np.testing.assert_allclose([row.iq for row in rows], shots_b.mean(axis=1))
```

前提是 B 和 mean 使用相同 seed、扫描与参数。若不一致，先比较请求，不要随意放宽误差。
**不要把平均结果交给原来的 `analyze_rabi`**：它需要 shot 分布估计误差；平均值不能还原它。

### 3．重开旧结果，故意检查一次不兼容

在 Notebook 保存 run 身份；文件位于项目根目录，可以用 VS Code 直接查看：

```python
import json

(project.root / "editing-runs.json").write_text(
    json.dumps({"before": run_a.id, "shots": run_b.id, "mean": run_mean.id}),
    encoding="utf-8",
)
```

重启 Notebook kernel，只重新运行连接单元和 `MeanRow` 定义，然后：

```python
import json

ids = json.loads((project.root / "editing-runs.json").read_text(encoding="utf-8"))
reopened = session.run(ids["mean"]).result().rows_as(MeanRow)
print(reopened[0].iq)
```

无需重新运行实验。再故意把数组结果按标量结构读一次：

```python
session.run(ids["shots"]).result().rows_as(MeanRow)
```

预期明确报类型不匹配，旧数据不变。不要靠强制 cast 绕过错误。对旧数组使用对应类型：

```python
from numpy.typing import NDArray


@dataclass(frozen=True)
class ShotRow:
    amplitude: sc.Quantity
    iq: NDArray[np.complex128]


old_rows = session.run(ids["shots"]).result().rows_as(ShotRow)
print(old_rows[0].iq.shape)  # (64,)
```

两批返回形状不同的记录仍各自可读，当前源码没有替换历史 schema。

## 选做短组合：出错、修复，再读旧结果

完成 B 后另选时间做，不必与参数扫描或维护题一起进行。将 `mean_iq` 中的
`return complex(iq.mean())` 暂时改为：

```python
raise ValueError("mean_iq practice error")
```

保存文件，执行 `session.refresh()` 后重新运行 import 行，从新声明创建请求、
prepare 并运行。这次应失败，Notebook 应同时显示计算节点、`ValueError` 和
`mean_iq practice error`；不是只告诉你 assembly failed。记下这次 job 的 receipt，
不要反复 run 来猜是否完成。

恢复原来的 return 行，再保存、刷新、新建请求并运行，应得到一次新的成功结果。
然后用前面保存的 `editing-runs.json` 从新内核读回旧结果。修复不会改写旧 run，
刷新本身也不会自动重试失败任务。若需要查 worker 日志才知道这段自写错误的原因，
记录为错误提示的体验问题。

## 另选一次短体验：新增实验

在 VS Code 复制 `src/my_experiment/teaching.py` 为 `extra.py`，将实验 ID 改为
`teaching.extra`，函数名 `teaching_rabi` 改为 `extra_rabi`，保存。无需先重启 kernel：

```python
session.refresh()
from my_experiment.extra import extra_rabi

request = extra_rabi().sweep(amplitude=np.linspace(0, 0.8, 7))
prepared = session.prepare(request, parameters=params)
print(prepared.preview.point_count)
```

预期 7 点；再按前面的方式运行和读取。试着修改新实验的默认值，再运行同样的
refresh/import 单元。首次导入和后续修改使用相同步骤；已有请求与已有 Python 变量不会
自行变成新版本，重新 import 后才取得新定义。导入前尚未刷新时，新文件仍不属于已接纳
快照；不要手写 reload 或改路径绕过。旧实验和旧 run 应继续可用。

## 反馈只记关键事实

每项记录：使用的交付 build_id、实际修改、预期/实际、停在哪一步、查了什么。
保留失败信息、job receipt 和 run 身份；无需把全部日志贴出来。尤其记录：是否仍有框架
代码不知道为什么要写；提示能否帮助定位；是否能在没有 AI 的情况下判断下一步。

做完 A 不代表 B 也通过；软件自动回归不代替你的易用性判断。首轮已经通过的标准流程
无需重复。结束时关闭 session，并用“停止实验服务”任务停止本次练习服务。

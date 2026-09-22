# 参数保存、扫描组合和分组分析：分三次体验

使用本次新版安装包**新建**的最小教学项目。不覆盖此前 A/B 的练习项目，也不要求重跑
完整标准课程；这些练习不需要 AI 助手、不连接真实设备。每次只选一个主题，约 15–25 分钟。
请先由维护者确认安装包与本指南版本一致。

在 VS Code 打开项目，选择项目 `.venv` 的 kernel，运行“启动实验服务”任务。
在 `notebooks` 新建 Notebook，连接单元为：

```python
import json
import numpy as np
import scopecat as sc
from lab_teaching.parameters import Drive
from lab_teaching.session import open_parameters

project = sc.open_project()
session = project.authoring()
session.refresh()
from my_experiment.teaching import teaching_rabi
from my_experiment.group_analysis import CurveSummary

params = open_parameters(session)
```

## C．保存参数，不再为每次修改起名字

```python
before = params.version
params[Drive]["q0"].frequency = 5.146
print(params.diff())
after = params.save(note="本次练习")
print(before, after, params.save() == after)
```

第三项应为 `True`：没有修改就复用原版本。`save("标签")` 表示另建命名分支，
日常保存只需 `save()`。重新打开 `open_parameters(session)` 会取教学工作区的最新值；
`session.parameters.get(before.id)` 精确读取旧版本，不追随分支最新值。
保存不会自动发布共享默认配置，已经 prepare 的请求也不会换成新参数。

**近邻变化：** 再改一次频率并保存。关闭连接后重开，能否找回最新值和指定旧版本？
记录哪个名字、按钮或返回值让你无法判断“当前值”和“历史版本”。本题到这里即可结束。

## D．两个幅度下扫频，再对每条曲线复用同一个分析

另开一次练习，仍使用返回原始 64 个 shots 的 `teaching.py`。若已经做过平均 IQ 题，
请另建新项目，不要求从旧 Notebook 中猜如何撤销修改。

```python
request = teaching_rabi().sweep(amplitude=[0.12, 0.24])
request = request.sweep_parameter(
    Drive.frequency, "q0", np.linspace(5.135, 5.155, 21), name="frequency"
)
prepared = session.prepare(request, parameters=params)
print(prepared.preview.point_count)
```

应为 **42 点**，不是 42×64 个扫描点；64 shots 留在每个点内部。
`Drive.frequency` 的字段声明提供 GHz 单位与行键；这里的覆盖只属于本次 run，
不会把参数工作区改成扫描的最后一个频率。`q0` 是这一合成教学表的行名，
不是让你替换真实设备参数的占位符。

```python
run = prepared.run().wait(timeout=120).result()
grouped = session.analyze_groups_as(
    run.id,
    "my_experiment.group_analysis:summarize_curve",
    CurveSummary,
    by=("amplitude",),
    fitting="frequency",
)
for group in grouped.groups:
    print(group.receipt.coordinates, group.receipt.error, group.value)
```

预期两组、各 21 点，峰所在采样点接近 `5.145 GHz`。打开
`src/my_experiment/group_analysis.py`：这是普通 NumPy 函数，对每组做相同计算。
当前只是找最高的采样点，**不是峰模型拟合或物理标定**。曲线表保留 shot 均值标准误差；
峰频率不确定度仍为 `None`，不能把前者冒充后者。

```python
curve = grouped.groups[0].publication.dataset("curve").to_pandas()
curve.plot(x="frequency", y="mean_i")
```

**近邻变化：** 把 `amplitude` 改成三个值，先预测点数再 preview；或者在新请求中使用
`sweep(mode="paired", amplitude=[0.12, 0.24])`，并将频率列表改成两个值。
配对模式只产生两点；两条扫描列表长度不同时会明确失败。不要拿两点结果做这份至少
需要三点的分析。失败后修正请求，再创建 preview，原来成功的 run 仍保留。

**短组合：** 修改分析的 `minimum_contrast` 默认值到 `2.0`，保存文件，执行
`session.refresh()`，再用上面的分析调用加 `source="current"`。新结果应拒绝候选，
旧结果不变。不传 source 时使用原 run 的源码。`repeats="combine"` 也不会自动平均；
重复扫描的归约规则应由科学函数明确选择。

保存身份后，重启 kernel/服务只读取，不重新采集：

```python
(project.root / "grouped-run.json").write_text(
    json.dumps(
        {
            "run_id": run.id,
            "publication_id": grouped.publication.id,
        }
    ),
    encoding="utf-8",
)
```

新 Notebook 只运行连接单元，然后：

```python
ids = json.loads((project.root / "grouped-run.json").read_text(encoding="utf-8"))
old = session.read_groups_as(ids["run_id"], ids["publication_id"], CurveSummary)
print([group.value for group in old.groups])
```

## E．用 GUI 整理研究历史

运行 VS Code 的“打开实验界面”任务，进入 **History**：

1. 输入名称创建研究项目，选教学样品后点 **Associate sample**。
2. 勾选 **Show runs outside this project to associate**，找到本次 run，点 **Associate run**。
3. 取消勾选，按样品筛选；打开 run 查看原始测量与分别保存的分析。
4. 新建第二个研究项目，关联同一个 run；重命名或移除其中一个关联。
5. 原 run、另一个项目里的关联和历史分析都应继续存在。样品关联不会自动收进所有历史 run。

这题不用写 CLI 或移动目录。记录能否看懂“关联”和“复制/删除”的区别，以及能否找到
原结果和后做的分析。完成后关闭连接，使用“停止实验服务”任务。

三题不必一次做完。反馈只需附练习名、你修改了什么、期望/实际结果和最先看不懂的位置；
不要求先让 AI 修好再反馈。维护者备份恢复另见 [MAINTENANCE.md](MAINTENANCE.md)。

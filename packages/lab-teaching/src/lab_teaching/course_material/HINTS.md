# 小提示：卡住时按题查看

先自己尝试，再看对应题；不需要阅读或修改 bootstrap。

1. `drive["q0"].frequency = 5.148` 是普通属性赋值。`5.148` 按字段声明解释为 GHz。
   `params.diff()` 只展示变化；`params.preview()` 检查单位和范围，不保存。
2. `np.linspace(0, 0.8, 21)` 有 21 点，两端都包含。用 `request.values["amplitude"] = sc.Scan(...)` 设置扫描，更改后重新运行预览单元。
3. 先看点和曲线，再看 `status`。`passed` 是教学拟合通过，不是实机标定通过。
4. 名字已存在时改为 `my-second-drive`。重开使用保存时打印的版本名；不要猜“最新”。
5. 只扫到 `0.1` 很可能看不到一个完整周期；先恢复 `0～0.8`，不要直接填入 π 幅度。
6. 两个不同 seed 产生独立教学噪声。相同 seed 和坐标会重放同一响应，不算独立验证。
7. 平线没有可用的振荡信息，增加拟合次数不能凭空得到候选。

若 `wait()` 超时，原采集不因此取消；保留 `job`，再次 `job.wait(timeout=120)`，不要重跑 `prepared.run()`。
内核重启后可以使用作业保留的 receipt 路径 `session.reopen(path)` 恢复任务，或用已知 run ID 重开数据。
首次加载超时、目录导入错误请记录完整异常和项目 `.scopecat/daemon.log`，交给维护者处理环境。

内核重启后，先重新运行第 0 题导入与连接，随后用之前打印的路径：

```python
job = session.reopen("之前打印的 receipt 绝对路径")
run = job.wait(timeout=120).result()
print(run.id)
```

这是恢复已有任务；不要创建第二个 `run()` 来代替恢复。

## 暂停后继续

完成第 0–4 题并重开验证后，可以结束第一轮。以后若要继续第 5–7 题且原内核已重启，
在 start.ipynb 新增单元执行下面代码。它恢复已有运行，不重新采集；分析会重新计算并画图。
这是提供好的恢复步骤，不要求初学者掌握书签文件格式。

```python
import json
from pathlib import Path
import numpy as np
import scopecat as sc
from lab_teaching.parameters import Drive
from lab_teaching.session import analyze_rabi
from my_experiment.teaching import teaching_rabi

project = sc.open_project()
session = project.authoring()
bookmark = json.loads(
    (project.root / "notebooks/first-run.json").read_text(encoding="utf-8")
)
params = session.config.workspace(context=bookmark["parameter_version"])
drive = params[Drive]
run = session.run(bookmark["run_id"])
report = analyze_rabi(session, run)
amplitudes = np.linspace(0.0, 0.8, 21)
request = teaching_rabi(shots=64, seed=200)
request.values["amplitude"] = sc.Scan(amplitudes)
```

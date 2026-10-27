# 收集本地命令诊断 {#collect-local-command-diagnostics}

用 `python -m scopecat_server.cli diagnose` 为明确选择的一条命令保留证据。
它不要求实验室项目、私有适配器或参考实验室。只检查公开 Python 环境时可运行：

```sh
python -m scopecat_server.cli diagnose --output diagnostics-first -- python -c "print('diagnostic ready')"
```

使用待检查环境中的 Python。输出目录必须是新目录；`--cwd PATH` 指定命令工作目录，
`--timeout 300` 将整条命令的时限设为 300 秒。`--` 后的参数直接交给进程，不经过 shell。
**命令本身可能访问设备**；诊断包装器不选择实验，也不是离线沙箱。
如果只需软件诊断，请明确选择不访问设备的命令。

命令失败时包装器返回非零退出状态，并保留原退出码、输出、超时状态、耗时和观察到的残留进程。
清理依据进程身份处理启动进程及观察到的后代，不停止无关实验室服务。
短命进程可能在两次观察之间派生后代；没有观察到残留进程不等于证明所有可能的泄漏都不存在。

先读 `report.md` 摘要，再查看 `command/worker.log` 原始输出。
`summary.json` 与 `command/outcome.json` 保留机器可读证据。
相邻 `.zip` 只包含选定诊断文件：报告与元数据、用例日志和事件、计时记录、启动诊断及内核日志。
项目目录、数据库、无关文件和符号链接不纳入归档。**不会自动上传任何内容**；
分享前自行检查命令、路径和日志内容。

## 添加工作阶段 {#add-workload-phases}

子进程会收到 `SCOPECAT_DIAGNOSTIC_DIRECTORY`、`SCOPECAT_TIMING_DIRECTORY`
和 `SCOPECAT_STARTUP_DIAGNOSTICS`。Scopecat 组件使用后两者写入已有计时和启动证据。
服务接受关闭请求后，启动诊断还记录关闭阶段；若五秒后进程仍未退出，会采集一次线程栈样本。
父进程生命周期记录包含启动器和端点的进程身份、观察到的子进程、关闭请求接纳及等待/终止超时阶段。
这些观察不改变关闭期限或清理策略，也不在快照中包含端点 URL 或关闭令牌。

工作负载可向诊断目录中的 `events.jsonl` 追加 JSON 对象：

```python
import json
import os
import time
from pathlib import Path

path = Path(os.environ["SCOPECAT_DIAGNOSTIC_DIRECTORY"]) / "events.jsonl"
with path.open("a", encoding="utf-8") as stream:
    stream.write(
        json.dumps(
            {
                "phase": "prepare",
                "status": "passed",
                "seconds": 0.25,
                "clock_ns": time.monotonic_ns(),
            }
        )
        + "\n"
    )
```

若要关联首次数据入库，记录带 `status="started"` 和 `clock_ns` 的 `submit` 阶段，
以及含 `run_id` 的 `run` 事件。报告会把该运行与计时日志中的
`first_measurement_ingested` 匹配。它测量数据到达 daemon 的时刻，不是 GUI 渲染时刻。
未完成阶段不能记作零耗时；格式错误或失败的事件仍按失败保留。
比较首次使用与后续调用时保留各个样本；新建进程不会清空操作系统缓存。

## 实验室专用诊断套件 {#laboratory-specific-diagnostic-suites}

维护者可复用 `scopecat_server.host_diagnostics.run_case`、`summarize`
和 `archive_diagnostics` 运行多个命名用例。显式传入工作目录，保留每个用例的结果，
将 `metadata["expected_cases"]` 设为计划总数，并只把实际用例名传给归档函数。
工作负载选择、适配器、离线约束及科学验收由实验室调用方负责。
这些工具收集软件证据，不构成科学资格化或持久数据兼容承诺。

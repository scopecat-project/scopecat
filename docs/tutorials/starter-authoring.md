# Edit and analyze a starter experiment

For your first Notebook, start with [Parameters and scans in Help](../getting-started/quickstart.md).
You can keep using that course folder. This guide is for a **separate author folder**
with the Settings starter, which uses different source and Notebook files.

In the application, choose **Settings → Author code → New code folder**, select
a save location and create it. Open the new folder in VS Code and select its `.venv`
interpreter. Keep Scopecat open and run `notebooks/02_edit_scan.py` cell by cell.
The example uses synthetic data and needs no devices.

Framework contributors can use the [source development launcher](../development/public-preview.md#daily-source-desktop).
It starts blank; create or register an author folder in Settings before following this guide.
The separate `scopecat init` starter remains available through the
[CLI reference](../reference/cli.md#project-lifecycle); it is not required for the
desktop lesson above. The Connect cell uses `sc.notebook(...)` to discover the
registered application; you do not need to copy a port number.

## 连接前后核对什么

Connect 单元格显示作者目录、已绑定的数据目录与数据身份、作者身份，
以及当前 kernel 的 Python 和 Scopecat 版本／来源。再次执行 `print(author)` 只读
已有会话信息，不发起采集，也不重新选择应用；它不是应用在线状态检查。
这些信息不含连接令牌，但路径可能包含用户名或实验名称，公开分享前请自行隐去。
Settings 中的执行 Python 是后台实验环境；Notebook 摘要显示的是当前交互内核。

- **应用离线**：打开原来登记的 Scopecat 应用，再连接；不要另外启动作者目录中的数据空间。
- **目录未登记**：在目标应用的 **Settings → Author code → Use existing folder**
  登记目录并明确执行 Python，再从独立作者 kernel 连接。
- **应用重启／旧 session**：先 `author.close()`，再运行 Connect。
  从 Data 的只读代码或已保存 receipt 找回原结果，不要用 Submit 找回结果。
- **错误 kernel／版本不兼容**：选择作者环境；必要时在 Settings 重建客户端，
  关闭旧 kernel 后重新选择。应用自用 Python 不是作者 kernel。
- **目标或旧 binding 冲突**：先核对原登记与 `scopecat.runtime.toml`，保留原文件和数据。
  不要删除登记记录来绕过身份校验，也不要把旧任务接到其他数据空间。

同一作者目录可以登记到多个应用。普通独立 kernel 使用已保存的默认关联；
应用启动的 kernel 继承该应用上下文。需要另一个**已登记**目标时，关闭旧会话后
显式使用 `sc.notebook(author_directory, application=application_home)`；
`application_home` 是应用的数据实例位置，不是 exe、安装目录或 Python 路径。
显式参数优先于继承上下文，继承上下文优先于保存的默认关联；离线或冲突不会自动改接其他应用。

## Your editable files

`src/scopecat_lab/authored/signal.py` contains a synthetic response, one experiment,
and one ordinary analysis function. `notebooks/02_edit_scan.py` supplies setup,
creates a request and prints a run link. This is a framework lesson, not a model
or calibration of a real qubit.

```python
params = open_parameters(author)
request = signal(center=0.0)
request.values["position"] = sc.Scan([-1.0, 0.0, 1.0])
prepared = author.prepare(request, parameters=params)
job = prepared.run()
run = job.wait(timeout=120).result()
```

Change the positions and rerun the preparation and acquisition. A `Scan` explicitly
selects an axis; a plain array does not. `prepare` captures the request. Changes
made later require another preparation. `request.copy()` creates an independent
request for comparison; creating or editing a request never starts acquisition.

The supplied three points produce `(0.5, 1.0, 0.5)`. The script publishes a
`Summary(mean=2/3, points=3)` using `analyze_as`, records the analysis source and
opens the retained run again through its receipt. Inspect the same run in the GUI.

`authored/parameters.py` owns the parameter model and opens the `starter` branch.
The `scale` column has a real effect on the response:

```python
params["response"]["signal"]["scale"] = 2.0
params.save(note="Compare a doubled response")
```

Repeating the same scan now gives `(1.0, 2.0, 1.0)`. Reopening a Notebook preserves
the saved branch values; it does not reset them to the source defaults. For a
disposable comparison, `params.save("trial")` forks the branch. Previous previews,
runs and analyses retain their original inputs. Startup, editing and running
create no global parameter default or artificial working point.

The separate CLI starter also includes `authored/thermometer.py`; its first Notebook
uses the same author-session preparation path. Both scripts load the project's
local package before refreshing its author modules, so ordinary imports work
when running a generated script from outside its source directory.

## Edit code and retain evidence

Request value changes need another prepare, not a source refresh. After editing
`authored/signal.py`, refresh and repeat the normal import:

```python
author.refresh()
from scopecat_lab.authored.signal import signal
```

Use the same steps for newly added author modules. There is no kernel restart or
manual reload step. Previously imported aliases and existing requests retain their
original definitions; create a new request from the newly imported declaration.
Keep the original run ID or receipt and reopen it instead of rerunning acquisition.

`analyze_as(run.id, "scopecat_lab.authored.signal:summarize", Summary)` selects the
run's original source by default. After refresh, `source="current"` explicitly
reanalyzes using the edited source. The registered analysis selector is still a
qualified string; this lesson does not invent an unreleased analysis shortcut.

A wait timeout ends this wait. Call `job.wait(...)` again or reopen the receipt;
calling `prepared.run()` again creates a new acquisition.

## Where to go next

Use [managed author sessions](../how-to/managed-author-session.md) for parameter
workspaces and saved plans, or choose another [Notebook lesson](teaching-sandboxes.md).
Device and quantum extension authors can follow the
[extension guides](../extensions/index.md).

## Rebuild this folder's client environment

After **Settings → Author code** prepares the local environment, keep
`requirements-client.txt` and `author-environments/` with your source. The lock
names its source snapshot: framework wheels copied from the selected delivery,
`client.in` (Scopecat and ipykernel inputs), `delivery-constraints.txt`,
`delivery.json` (delivery/source provenance), and `python.json` (Python version,
ABI and binary identity). `pyproject.toml` declares additional experiment
requirements; it is not by itself the complete client environment.

Close the Notebook. From the author directory, create a venv with the matching
Python version/ABI recorded in `python.json`, then install the exact client lock:

```sh
uv venv --python <matching-python> .venv
uv pip sync --python .venv --require-hashes requirements-client.txt
```

On Windows this creates `.venv/Scripts/python.exe`; choose that interpreter/kernel
when reopening the Notebook. These commands do not activate a shell environment
or install into the application. A different OS/ABI needs its own preparation;
this lock is not a cross-platform or fully offline distribution. Registry packages
need network access or a populated cache. Custom indexes and third-party local
sources remain explicit prerequisites. Retaining the framework wheels allows the
folder to move without retaining the original application installation path.

To change dependencies, edit `pyproject.toml`, then explicitly prepare/rebuild in
Settings. Preparation checks the selected delivery constraints and retains a new
client lock; incompatible requirements fail rather than upgrading shared framework
dependencies. If you own a `uv.lock`, update it deliberately first: stale locks
are rejected. Ad hoc pip installs are not represented by the saved lock. Keep a
copy of the previous lock when you need to reproduce that client later.

This rebuild affects the editable client only. Published experiments retain fixed
source and application-owned execution environments; changing the client does not
replace their dependencies or reinterpret old results. Prepare and select the
execution environment through the existing application workflow for new source.

## From the lesson to a first real experiment

Start with Help's **Parameters and scans** lesson, then create the separate
Settings author folder described above. Run its synthetic three-point scan once
and keep its receipt. The ordinary template uses `sc.notebook(...)`, the existing
unified session entry. Registration now saves the application association outside
the source folder, so an independently opened kernel can find it without a runtime
file or copied port. Use the connection checks above before editing or acquiring.

| Keep the learned workflow | Replace for the laboratory |
| --- | --- |
| Requests, explicit scans, prepare/preview, submit, wait, receipt reopen | The analytic response function with a supported acquisition/driver implementation |
| Parameter workspaces, units, saved branches and analysis publications | Scientific parameter schema, units, limits, scan range, timing, averaging and calibration assumptions |
| Experiment/analysis API structure | `configuration.py`'s synthetic setup: real topology, instrument registry, routing, capabilities and lifecycle policy |
| Editable source and declared dependencies | Required adapters, SDK versions, native drivers and site-specific prerequisites in the appropriate client/execution environments |

Changing an address cannot turn this synthetic response into a physical
measurement. Review the setup and driver contract with the equipment owner,
validate units and small bounded scan inputs, and preview before explicit
acquisition. Choose device exit/failure policy according to the actual equipment;
this lesson does not supply a laboratory default. Keep private addresses,
credentials, SDKs and site parameters outside the public source repository.
After the first intended acquisition, record its receipt and reopen the same
result; do not submit again merely to find it.

The bounded Windows user observation remains **unperformed**. Record the app
version, Python/ABI, selected environment lock, assistance needed and concrete
blockers while a target user rebuilds, edits, runs, then exits and reopens the exact
result/source using the existing recovery workflow. Automated Linux rebuild and
kernel checks do not establish this observation or real-device compatibility.

# Author folders and project layout

## Author folders

For a first Notebook, use [Parameters and scans in Help](../getting-started/quickstart.md).
Help creates and registers an editable folder with `notebooks/parameters.ipynb`
and source under `src/my_experiment`. Continue using that folder for your own work;
**Manage this code folder in Settings** opens its registration and Python settings.

For a separate directory, **Settings → Author code → New code folder** creates
this device-free starter and prepares its independent `.venv`:

```text
my-experiment/
├── README.md
├── scopecat.toml
├── notebooks/
│   └── 02_edit_scan.py
└── src/scopecat_lab/
    ├── __init__.py
    ├── configuration.py
    └── authored/
        ├── __init__.py
        ├── parameters.py
        └── signal.py
```

The Settings starter's manifest declares its editable author modules:

```toml
[authors]
modules = ["scopecat_lab.authored"]
dependencies = []
source_roots = ["src"]
```

Help lessons use their own source names and may declare execution capabilities in
`[lab.capabilities]`. Follow the Notebook supplied with your folder; the Help and
Settings starters are separate examples. Both share the running application's data
and device ownership. Neither requires starting a service for that folder, and
scientific records are retained independently of these code directories.

Select the folder's independent Python in your editor. Save and refresh source as
shown in its Notebook. To register existing laboratory code or repair an environment,
see [author folders in the application](../how-to/maintain-application.md#author-folders).

## Independent service projects

The remaining layout and deployment commands are for maintainers composing a
laboratory-owned service or working with an existing standalone project. They are
not the folders created by Help or Settings. See [CLI entry ownership](cli.md#entry-ownership-and-migration)
for the required Python environment and server-only command spelling.

`scopecat init my-lab` creates this minimal project:

```text
my-lab/
├── README.md
├── scopecat.toml
├── notebooks/
│   ├── 01_first_run.py
│   └── 02_edit_scan.py
└── src/scopecat_lab/
    ├── __init__.py
    ├── application.py
    ├── backend.py
    ├── configuration.py
    └── authored/
        ├── __init__.py
        ├── parameters.py
        ├── signal.py
        └── thermometer.py
```

Projects may add a `config/` directory for external, version-controlled
infrastructure inputs. Runtime state lives in `.scopecat/` and is owned by the
daemon rather than edited by users.

The runtime directory contains `control.sqlite3`, its SQLite side files, the
immutable `objects/` store, daemon metadata, and logs. The daemon refuses schema
versions other than its own and never implicitly migrates or deletes state.
Use the supported [stopped-project snapshot workflow](../how-to/backup-and-restore.md)
to preserve the database, objects, application/configuration source and installed
version record together. This supports only the current format. Owners may retain
old environments separately for archival reading; new builds do not promise to
read or migrate earlier development stores.

### Manifest

`scopecat.toml` identifies daemon bootstrap, declared execution capabilities,
and the instrument backend:

```toml
[lab]
bootstrap = "scopecat_lab.application:create_bootstrap"
instrument_backend = "scopecat_lab.backend:create_backend"

[authors]
modules = ["scopecat_lab.authored"]
dependencies = ["scopecat-instruments"]
source_roots = ["src"]
```

Bootstrap and backend factories use `MODULE:CALLABLE` syntax. Optional capability
symbols in `[lab.capabilities]` use `MODULE:SYMBOL`; `[authors].modules` lists
the starter's discoverable author modules.
Project discovery searches at or above
the supplied path and makes the project's `src` directory importable.

### Source ownership

Author source and refresh roots are relative subdirectories; equivalent `./src/`
spellings are normalized. The whole project directory (`.` and its aliases) is
not a source root. Captures exclude `scopecat.runtime.toml` at every depth: local
application/data bindings are deployment metadata, not scientific source or
configuration-sharing attachments. Existing retained originals are not rewritten.

- `application.py` exports the lightweight initial configuration bootstrap.
- `[lab.capabilities]` declares notebook and worker execution capabilities without
  requiring a custom application factory.
- `backend.py` composes worker-only instrument providers and drivers.
- `configuration.py` declares equipment for first use, without parameter values.
- `authored/parameters.py` declares the starter's parameter model and opens its
  independent editing branch. Existing saved branch values are not reseeded.
- `authored/` contains locally editable experiment and analysis declarations. Source
  refresh captures these edits; it does not upgrade installed shared packages.
- `notebooks/` contains user-owned interactive workflows and scripts.

After initialization, these are application source files: edit, test, and
version them with the rest of the lab project. Use the
[setup management](../how-to/maintain-executable-setup.md) to activate reviewed
equipment changes and [parameter branches](../how-to/parameter-branches.md) for
ordinary author edits. The generated starter publishes no global parameter default.

Declare procedure, schedule, calibration, publication, and system-builder symbols
in `[lab.capabilities]`. They are resolved in the notebook or project worker, not
while the daemon loads its bootstrap. Importing the bootstrap factory must not
load user execution callbacks. A custom `lab.application` factory remains an
alternative for special composition; it cannot be combined with the capabilities
table.

### Bind a workspace to persistent local data

A local deployment binding is optional and separate from captured scientific
source. Without it, data and runtime state remain in `<workspace>/.scopecat/`.
To replace code directories while retaining one data space, put this file beside
`scopecat.toml` in each selected workspace:

```toml title="scopecat.runtime.toml"
[runtime]
data_root = "../laboratory-data"
deployment_root = "../bench-owner"
```

Both paths are required and resolve relative to the workspace; absolute paths
are also accepted. `data_root` directly owns `control.sqlite3`, immutable objects,
source materializations, author-job receipts and diagnostics. `deployment_root`
holds the local deployment identity and exclusive ownership lock. Use one
maintained deployment location for every launch path to the same bench. Separate
invented locations cannot detect that their SDKs address the same physical device.
Do not put either directory inside an authored source root.

Only one service owns a data space, and only one data space at a time owns a
configured deployment. Opening a project does not install dependencies or start
acquisition. Notebook endpoint overrides must match the registered source owner,
data space and deployment; a URL alone does not select another codebase.

Within the current format, store identity persists independently of paths; runs, parameters and
source hashes are retained. Original code execution still requires its recorded
environment and retained source snapshot. Retaining data does not promise execution
of arbitrary old code in a new runtime. This is local execution, not an independent
remote client/server environment.

当前 schema 是开发期数据格式，尚未被指定为长期兼容基线。
[备份与恢复](../how-to/backup-and-restore.md)支持当前格式；schema 115 可在启动恢复页选择备份并原地升级至 116。
其他早期格式仍拒绝打开且不修改原件，具体边界见[数据兼容策略](../development/data-compatibility.md)。

### Register another author workspace

Use this when two codebases should publish and execute against the same local
service and scientific records. Each registered author source has a qualified
execution environment, independent of the application Python. Experiments, shared
helpers and compiler source may differ and use the same Python package names.
Register from the service's installed environment while both deployments are stopped:

```shell
scopecat register-workspace /path/to/second --service /path/to/service
```

Replace the two paths with your source directories; quote paths containing spaces.
The command saves a per-user application association and returns the registered
workspace ID without writing runtime paths into the author folder. The first
association remains the default; registering another application does not replace
it. It refuses to redirect a workspace with its own scientific
store or a conflicting explicit binding. Keep starting, stopping and snapshotting the shared deployment from its service
workspace.

Open each codebase normally from its notebook:

```python
import scopecat as sc

session = sc.notebook()
print(session)  # Captured data/source identities and current client Python/framework.
```

The saved association selects the registered application. An explicit
`sc.notebook(application="/path/to/service")` selects another registered target
after closing the old session; it does not transfer existing jobs or data.
Application-launched kernels inherit that application's context. Explicit target
overrides inherited context, which overrides the saved default. Refresh updates
only that owner's publication head; prepared work and saved plans retain their
exact source. Both sessions share the service's data and may select the same record
collection. They do not need separate daemons. The workbench currently defaults to
the service source and preserves a saved plan or analysis handoff's source owner;
a general workspace selector is not included yet.

Registration is local machine configuration, separate from retained scientific
membership. Snapshots preserve source references and bundles but exclude local
workspace paths. After a current-format restore or move, read retained data without recreating
those paths. To execute again from a new qualified location, stop the service and
explicitly rebind an existing identity:

```shell
scopecat register-workspace /new/source/location --service /path/to/service --identity EXISTING_WORKSPACE_ID
```

Use the ID from the original registration. This keeps its publication history and
revokes the old source location. Unknown IDs cannot be rebound this way; every
source uses an explicit registered identity. Retained execution requires its captured dependency identities, even
when the application and author environments are separate.

## Submit an ordinary procedure from an author session

普通作者入口是 `session.procedures`，注册的应用作者目录也使用同一入口。
先在源码清单 `[lab.capabilities].procedures` 中登记 `module:attribute`。
脚本中先选择源码，再导入 definition 和 intent：

```python
with project.authoring() as session:
    session.refresh()
    from my_lab.workflow import ReviewIntent, review

    prepared = session.procedures.prepare(review, ReviewIntent(label="Review scan"))
    task = prepared.submit()
    print(task.id, task.dispatch_error)
```

省略 `request_key` 时，每次 `prepare` 生成一个完整随机 key 并保存在
`prepared.command.request_key` 中。同一个 prepared 的提交、重试与 reconnect
保持同一命令、key 和固定源码；另一次 `prepare` 是另一请求，不是重试。
需要业务幂等键时显式传入 `request_key="review-scan-1"`，原有显式键语义不变。
默认 key 不表示归属，也不会自动恢复历史；需要跨 kernel 精确查找时，保留任务 ID、
显式业务键或原命令。课程的 prefix 键用于既有历史展示，仍然保留。

`sc.notebook()` 提供相同 API，并在 cell 边界选择源码。准备请求不刷新导入、
不执行 procedure。源码修改或旧 definition/model 别名需要显式 refresh/reimport；
科学选择由 intent 提供，不隐式套用 session 的 experiment 默认值。

提交前保留 `prepared`（或序列化的 `command`）。响应不确定时，重试这个精确命令：
`prepared.reconnect(session).submit()`。它保留原源码和身份，拒绝连接不同数据存储或
部署，即使 workspace ID 相同。修改命令不能复用旧 key。应用先校验固定源码中的
注册定义与规范 intent，再负责 dispatch；关闭客户端或 kernel 不会取消应用持有的任务。

已有任务按 ID 重开：`task = session.procedures.get(procedure_id)`。
高层历史查询支持服务端精确过滤；每页都带回相同过滤条件：

```python
page = session.procedures.list(request_key="review-scan-1", state="closed", limit=20)
for item in page.items:
    print(item.procedure_run_id, item.request_key, item.state)
if page.next_cursor is not None:
    page = session.procedures.list(
        request_key="review-scan-1", state="closed", limit=20, cursor=page.next_cursor
    )
```

`request_key` 是精确匹配，不是 prefix；`state` 是读取时的状态过滤。
空页可能表示没有这个请求，或其状态不符合过滤条件，不应改取“最新”任务。
并发状态可以变化；列表不是自动恢复或继续执行的授权。
不传过滤条件仍返回有界历史页，通过 `next_cursor` 翻页，再按明确 ID 重开。

`task.progress()` 读取进度。`task.wait(timeout=60)` 等到关闭、等待输入或需要处理，
返回保留状态，包含科学失败；超时只停止等待，不取消或重试。
等待问题可先捕获 `task.pending_input(..., schema=...)` 再答复，保存答复与继续执行
仍是不同动作，见[人工答复指南](../how-to/record-experiment-decisions.md)。
`task.resume()` 显式派发可继续工作；`task.cancel(actor="author", reason="Stop")`
显式请求取消。dispatch 错误仍保留已接纳的任务 ID，供检查与显式继续。

只读结果入口按 step key 选择最新 attempt，要求它已成功且有正确类型的输出：

```python
measurement = task.measurement("baseline")  # existing Run facade
snapshot = measurement.snapshot
verification = task.analysis("verify")     # existing PublishedAnalysis
print(verification.id, verification.fact("decision").value)
```

`measurement` 返回既有 Run facade；`analysis` 根据保留引用的 run／sample／project
来源读取精确 analysis record ID，并校验 ID 与来源，不选择同逻辑 key 的新版本。
两者复用 `output` 的跨页查找，不会从最新未完成 attempt 回退到旧成功结果。
step 不存在、尚未成功或输出类型不符会报错；读取不会采集、分析、发布或继续执行，
也不导入作者源码。新 session 重开任务后可再次读取；lazy facade 的后续读取仍需要
其连接存活。`task.steps()` 与 `task.output(...)` 保留底层证据访问，publication
凭据等其他结果仍通过 `output` 查看。校准历史与能力报告仍使用
`session.calibration_checks`。

The calibration and joint-calibration lessons use this managed path, including
closing the Notebook connection while application workers continue. Reconnecting
can read an old request immediately. Before preparing new work in that session,
explicitly reimport definitions and intent types; aliases from a closed session
are not rebound automatically.

`Project.connect()` and `LabClient.procedures.start/resume` retain their explicit
local Python execution semantics, including internal worker use. They are not
aliases for this managed API. The task-calibration lesson still requires a separate
migration of source-bound stage and finalization admission. The two client classes
retain their distinct execution responsibilities.

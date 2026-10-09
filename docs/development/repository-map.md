# Repository map

The [public application contract](architecture/public-application.md) owns the
product boundary; [desktop-product](architecture/desktop-product.md) records the
selected Python/WebView host. This map describes their existing implementation.

| Path | Responsibility and dependencies |
| --- | --- |
| `packages/scopecat` | Domain-neutral authoring, SDK/client APIs, planning, execution and records. Does not import the server or application host. |
| `packages/scopecat-server` | Project command composition, local HTTP/SSE transport, durable services/storage, execution and instrument worker ownership. Depends on core, not `lab_tools` or optional domain packages. |
| `packages/lab-tools` | Installed application entry, native windows and long-lived application composition; also build/delivery and acceptance tools. Depends on core/server/instruments/teaching. These roles share a package but are not all build-time code. |
| `packages/lab-teaching` | Maintained practice definitions and teaching resources consumed by the application and existing tutorial tooling. |
| `packages/scopecat-instruments` | Typed instrument capabilities, drivers, transports and virtual devices. Vendor SDKs belong to their explicitly prepared environments. |
| `packages/scopecat-quantum` | Hardware-independent quantum building blocks and target contracts. |
| `apps/scopecat-ui` | React/Vite workbench loaded by the native host; owns its Node graph and component/browser checks. |
| `examples/reference_lab` | Retained scientific/device integration and acceptance fixtures; see the [fixture map](reference-fixtures.md). |
| `testing/scopecat-testkit` | Shared test support with explicit package boundaries. |
| `fixtures` | Test-only serialized inputs. |
| `scripts`, `.github/workflows` | Repository generation, quality, acceptance and release orchestration; not installed runtime entry points. |
| `docs` | User, extension, reference and contributor contracts and evidence. |

## Entries, processes and delivery

| Role | Implementation / consumer |
| --- | --- |
| Wheel-installed command line | Within the installing Python environment, `scopecat` is owned by `scopecat-lab-tools` (`lab_tools.public_cli`). It composes server project commands with `app`, `notebook`, `teach` and tutorial `init --topic`, loading application implementations only on dispatch. |
| Server-only development | `python -m scopecat_server.cli` retains project init/config, lifecycle, workspace registration, automation, diagnosis and snapshot commands. Installing only the server does not install the application console script. The retained pilot bundle uses this module entry for isolated-installation acceptance; its CI use does not establish a separate product commitment. |
| Native entry and windows | `lab_tools.native_bootstrap` → `desktop` → `ApplicationRuntime`; window closure does not end retained execution. See [application host](architecture/application-host.md). |
| Long-lived ownership | `ApplicationRuntime` composes one application home; server lifecycle/runtime owns the durable writer and worker processes. Author folders do not imply per-source daemons. |
| Author/SDK execution | `author_environment`, execution environment preparation and server worker admission keep user Python/vendor SDKs separate from application Python. Core APIs stay usable without importing the host/server. |
| Build and delivery | `preview`, `delivery`, `native_package` assemble wheels, GUI and native payloads. `bundle.py` owns manifest/hash verification and offline installation and is copied as standalone `install.py`; it also supplies runtime helpers. It must remain usable without importing the application package. |
| Acceptance | `lab_tools.verify_*`, native/desktop journey tests and acceptance workflows exercise isolated installations, homes and environments. Software results do not substitute for #616 human/editor/device observations. |
| Existing tutorial consumers | `scopecat-lab` (`lab_tools.cli`), `project` and generated VS Code tasks remain callers pending ordinary-author convergence. Their presence in tests does not establish a permanent product entry. Update them together after a demonstrated replacement. |

### Teaching source convergence

Help's supplied Notebook topics route (`notebook_journey.py` → `lab_teaching.lessons.install_lesson`)
generates editable Notebooks, experiment or procedure modules, `parameters.py`,
`response.py`, `setup.py` and `workspace_app.py`; scan definitions also remain editable. Its
experiment code does not import `lab_teaching` scientific definitions.

Help uses one `NotebookJourney` receipt shape and preparation/Continue path,
keyed by the admitted topic. The original `learning/parameters.json` receipt
remains valid without rewriting it. Continue checks the registered source and
existing client Python, then opens the same Notebook; it does not execute cells.
Each fresh admitted course installs its own named teaching setup/parameter branch
through editable initialization code. Source, SDK and execution registration remain
ordinary author work in the existing application.

| Topic | Editable material / registration difference | Help readiness |
| --- | --- | --- |
| parameters | Synthetic experiment, response and parameter declarations; independent identity/setup helper | Supported, including existing receipts |
| groups | Same experiment and independent setup helper; `group_analysis.py` and `result_types.py`; no new procedure registration | Supported; separate preview/acquisition/read cells, saved run and publication IDs for reopen |
| refresh | Same base experiment plus `examples/extra.py`, copied explicitly by the learner | Supported; independent inputs, saved source adopted for new requests, separate preview/acquisition and exact retained reads |
| compute | Replaces `teaching.py` with raw/mean IQ experiments and typed results | Supported; independent inputs, separate acquisition/read cells and exact run ID or number selection after restart |
| calibration | `calibration.py`; explicit calibrate/check procedure list replaces the experiment | Supported; independent setup/catalog/trial identities and read-only paginated request history |
| joint-calibration | Adds `joint_calibration.py` and its procedure registration | Supported; independent identities and read-only request history; coupled verification remains visible |
| task-calibration | Adds `task_calibration.py`, task-stage/finalization procedures | Supported; shared preparation, independent identities and read-only task history |

All supplied topics share the application data and editable-source contract; there
is no independent service, per-course state machine or course-scoring system.
The topic list reflects current capabilities, not a fixed syllabus: course order,
difficulty and possible regrouping remain teaching-design work. Existing folders
are never overlaid with new material. This source integration is distinct from
native editor bridge, installed-distribution and unfamiliar-user acceptance.
The installed teaching package supplies generation resources, not hidden user
experiment implementations for that lesson.

The default `lab_teaching.project.create_project` and topic generation now share
`lab_teaching.lessons.install_lesson`. New default folders contain the same editable
parameter declarations, initialization, response and experiment as the topic
resources, plus local analysis/report helpers. `start.ipynb` and `reopen.ipynb`
retain their scan, analysis and history exercises; analysis uses the run's retained
author revision. The package's duplicate scientific implementations are removed.

The consumer audit covered `lab_tools.public_cli` (`init --topic`), `lab_tools.cli`
(`create`), generated VS Code tasks, `verify`/`verify_editing`/`verify_groups`, and
the calibration/maintenance and Notebook regression callers. These callers now
select their material at creation instead of overlaying a default course. Material
installation rejects nonempty author/Notebook content; existing folders and data
are not migrated. Older folders that import `lab_teaching` scientific helpers
must retain their original environment or explicitly move their edits to a fresh
folder. New folders import `my_experiment` instead.

This is material convergence, not lifecycle retirement: the existing CLI and VS
Code tutorial tasks still support explicit standalone service operation. They are
not generated by Help, whose teaching sources use the same
application/data owner. Their broader replacement remains a separate decision;
regression coverage is not a permanent product commitment.

### Standalone teaching entry consumers

| Caller | Entry and responsibility | Replacement boundary |
| --- | --- | --- |
| `public_cli.init --topic`, `cli create`, teaching journey tests | `lab_tools.project.create_project` / `lab_teaching.project.create_project`: fresh standalone scaffold, with installation identity added by the tools wrapper | Help creates a registered source through `notebook_journey.create_lesson_source`, not this standalone scaffold. |
| Generated `.vscode/tasks.json` | `lab_tools.cli prepare/start/open/stop`: install the project environment, explicitly operate its service and open its GUI | Retained for standalone folders. Help generates no service tasks and cannot Continue an unregistered standalone project. |
| `environment.prepare_project` | `cli check`: validate installed source identity and teaching-project admission after preparation | Retained; same-application source registration is a different admission path. |
| `acceptance.yml` → `scripts/verify_teaching_delivery.py` | Installed Help first checks the empty workbench, real practice cleanup and runtime requalification, then lessons/grouped recovery; `verify_project(groups=False)` retains the default course, generated prepare task and wrong-kernel guard | The carrier requires the same invocation’s successful application-check receipt. The separate installed-application wrapper is retired; default-course recovery and editor-task checks remain, and direct `cli verify` still runs its complete standalone check. |
| `verify.py`, `verify_maintenance.py`, editing/grouping/calibration journey tests | `verify_editing`, `verify_groups`, maintenance helpers: execute shipped cells and check exact retained results after reopening | Live verifier dependencies, not extra user teaching entries. |
| `lab-tools/pyproject.toml` | `scopecat-lab` console → `lab_tools.cli.main`; generated tasks use the same module directly | The console is documented in the standalone default README. Removing it is not justified by module-based CI alone. |
| Desktop Help → `notebook_journey.prepare` | Shared lesson resources, client/execution environments and registration in the current application | Help owns its receipts and Continue behavior; it does not adopt the standalone service or its data. |

The installed Help application validates an empty workbench both before practice
and after runtime requalification, before any lesson acquires data. Practice uses
the verified installation cache; the subsequent restart switches to a still-empty
author cache, and snapshot recovery keeps its separate cold cache. A dedicated
registered scaffold and notes retain exact file hashes and source identity through
requalification and the Help restart. Partial `help/acceptance.json` receipts are
written on failure and retained by the existing always-upload step; incomplete
checks cannot authorize omission of the former standalone application carrier.

The standalone path remains a maintainer fixture and compatibility call path
with existing generated-project consumers, not a commitment to a second teaching
product. Ordinary learning starts in Help. Standalone topic READMEs describe their
generated tasks and local `.scopecat` data; Help's topic READMEs describe the current
application and Continue. Shared resources do not make these lifecycles
interchangeable. Once the remaining consumers migrate with their installation,
kernel and recovery checks preserved, unused standalone entries can be removed.
Correcting their instructions and admission consistency does not reduce the
number of entries or establish single-entry convergence.

Standalone admission accepts the experiment-only capability declaration and the
three exact procedure combinations generated for `calibration`,
`joint-calibration` and `task-calibration`. Generation and admission share those
declarations; unknown, incomplete or mixed procedure combinations and extra
capabilities remain rejected. The admission check does not start a service or
establish installed-delivery acceptance for every topic. Help registers those
procedures through its existing application path.

For this boundary, `scripts/verify_teaching_delivery.py` compares all 13 default
source, Notebook and guide resources with the checkout after the installed CLI
creates its existing editor-check project, before preparation or edits. This
rejects stale wheel resources without creating another environment or running
extra kernels. The same verifier retains the six legacy course/reopen stages
and default snapshot recovery. The retired resource-check wrapper had no CI
consumer; its removal consolidates maintenance rather than reducing CI runtime.
`scripts/verify_notebook_journey.py` separately checks Help's real browser/kernel,
source edits and restart/Continue without reacquisition in the same application.
These reject stale installed teaching resources. The added topics also have focused
real-kernel editing/calibration restart tests. `scripts/verify_teaching_help_topics.py`
checks all five added Help entries and preserved edits after an application restart
with real Chromium, substituting development environment provisioning and editor
activation. None qualifies a new installed distribution, native editor clicks or
unfamiliar-user observation.

Native installation currently exposes a desktop entry, not a global PATH console.
The wheel console above is not a requirement to install application ownership in
every author venv. An application-supplied optional launcher targeting the same
application/data owner is the confirmed target, not yet implemented; see
[installation identities](installation-layout.md#application-command-and-data-identities).

The root workspace owns dependency locking and cross-package checks. Each Python
package owns its build metadata and focused tests. `uv run lint-imports` enforces
core independence and the server → application prohibition, including imports
inside command functions. CLI tests cover command composition, lazy startup and
installed entry-point ownership. The dependency direction is application → server
→ core; no optional reverse import or duplicate console-script owner is needed.

See [daemon architecture](architecture/daemon.md) for durable ownership and
[installation layout](installation-layout.md) for installed files, user data,
environments and disposable build/test outputs. This mapping does not prescribe
another package split or a different native host.

## Generated contracts

| Producer | Verification / regeneration |
| --- | --- |
| Python HTTP models and routes | `pnpm --dir apps/scopecat-ui run check:api`; use `generate:api` to update `src/api-schema.d.ts` |
| Measurement Arrow codec and testkit fixture | `uv run python scripts/generate_ui_measurement_arrow_fixture.py --check` |
| Instrument declarations and client generator | `uv run python scripts/generate_instrument_clients.py --check` |
| Reference acceptance producer | `uv run python scripts/generate_reference_lab_acceptance.py --check` |

Omit `--check` to regenerate the Python-produced fixtures. Review changes against
the producer; resolve generated-file conflicts by regenerating from the resolved
source. The [fixture map](reference-fixtures.md) describes their coverage.

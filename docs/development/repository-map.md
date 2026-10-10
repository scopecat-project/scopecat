# Repository map

The [public application contract](architecture/public-application.md) owns the
product boundary; [desktop-product](architecture/desktop-product.md) records the
selected Python/WebView host. This map describes their existing implementation.

| Path | Responsibility and dependencies |
| --- | --- |
| `packages/scopecat` | Domain-neutral authoring, SDK/client APIs, planning, execution and records. Does not import the server or application host. |
| `packages/scopecat-server` | Project command composition, local HTTP/SSE transport, durable services/storage, execution and instrument worker ownership. Depends on core, not `lab_tools` or optional domain packages. |
| `packages/lab-tools` | Installed application entry, native windows and long-lived application composition; also build/delivery and acceptance tools. Depends on core/server/instruments/teaching. These roles share a package but are not all build-time code. |
| `packages/lab-teaching` | Maintained practice definitions and teaching resources consumed by ordinary author folders and scientific regression fixtures. |
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
| Teaching CLI | `scopecat init --topic` uses Help’s ordinary editable source generator. Settings or existing `app` author commands prepare/register the folder; no teaching service tasks are generated. |

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

### Teaching entry and acceptance

`init --topic` and Help use `notebook_journey.create_lesson_source`. The CLI
creates source only; Settings adds the directory and prepares its ordinary client
and execution environments. Help owns its own receipts and Continue behavior;
CLI-created folders are opened as registered author directories. Both retain
editable source and notebooks and use the application's data owner.

The former `scopecat-lab` console, generated service tasks and standalone
preparation/verification wrappers are removed. Existing standalone folders must
keep their original software environment and data or explicitly transfer edits;
there is no automatic migration. `lab_teaching.project` remains a small regression
fixture for scientific and kernel tests, without generated lifecycle tasks.

`scripts/verify_teaching_delivery.py` runs installed Help acceptance, then checks
CLI source identity and ordinary author preparation against that application.
The installed Help carrier covers wrong kernels, source/Notebook edits, restart,
exact-result reads without reacquisition and cold-cache snapshot recovery.
`verify_editing` and `verify_groups` retain evidence appended to shipped lessons,
not separate course implementations. Focused refresh/compute/calibration kernel
journeys and `scripts/verify_teaching_help_topics.py` cover the other supplied
material. Duplicate standalone course/recovery stages and their admission tests
are retired with their entry points.

Offline bundle verification and installation remain in `bundle.py`; ordinary
author environments retain their independent interpreters and existing user edits.
These checks do not establish native editor activation, unfamiliar-user learning
or physical-device acceptance.

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

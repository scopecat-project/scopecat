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

Help's parameters route (`parameters_journey.py` → `lab_teaching.lessons.install_lesson`)
generates an editable Notebook, `teaching.py`, `parameters.py`, `response.py`,
`setup.py` and `workspace_app.py`; scan definitions also remain editable. Its
experiment code does not import `lab_teaching` scientific definitions.
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
not generated by Help, whose parameters source continues to use the same
application/data owner. Their broader replacement remains a separate decision;
regression coverage is not a permanent product commitment.

For this boundary, `scripts/verify_default_teaching_journey.py` compares freshly
generated source and original Notebook cells with the checkout before running the
six legacy course/reopen kernels from a wheel-installed delivery. Run it with
that delivery's installed Python, a fresh author directory and the delivery path.
`scripts/verify_parameters_journey.py` separately checks Help's real browser/kernel,
source edits and restart/Continue without reacquisition in the same application.
Both reject stale installed teaching resources; neither qualifies native editor
clicks or unfamiliar-user observation.

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

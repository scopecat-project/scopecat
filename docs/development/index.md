# Develop Scopecat

This section is for contributors changing the Scopecat repository itself.

## Set up the workspace

Development requires Python 3.14 or newer and uv:

```sh
uv sync --locked
```

Add `--group notebook` only when a local Jupyter kernel is needed. The React
project console has its own locked pnpm workspace under `apps/scopecat-ui`.

## Run repository checks

```sh
uv run pytest
uv run basedpyright
uv run lint-imports
uv run ruff check .
uv run ruff format --check .
uv run --group docs zensical build --strict
```

Run one member's suite with its declared dependency group when iterating:

```sh
uv run --locked --package scopecat --group test pytest packages/scopecat/tests
uv run --locked --package scopecat-server --group test pytest packages/scopecat-server/tests
uv run --locked --package reference-lab --group test pytest examples/reference_lab/tests
```

## Repository and architecture

- [Current platform status and remaining work](platform-status.md)
- [Calibration branch integration boundary](calibration-branch-closeout.md)
- [Multi-target calibration and maintenance](calibration-maintenance.md)
- [Automation tasks and durable execution](architecture/automation-tasks.md)
- [Task parameter flow and acceptance](architecture/task-parameter-flow.md)
- [Repository map](repository-map.md)
- [Public previews and source development](public-preview.md)
- [Core workflow evaluations](workflow-evaluations.md)
- [Everyday Python author contract](everyday-author-contract.md)
- [Supervised laboratory pilot roadmap](lab-pilot-roadmap.md)
- [Pilot work slices and acceptance fixtures](pilot-work-slices.md)
- [Architecture](architecture/index.md)
- [Experiment workbench and session contexts](architecture/experiment-contexts.md)
  (implemented boundaries and selected direction)
- [Structured authoring direction](architecture/structured-authoring.md) (proposal)
- [Scalability benchmarks](scalability.md)
- [Project charter](project-charter.md)

Package inventories, generated-code rules, and implementation contracts stay in
package READMEs and docstrings beside the code that owns them.

Use the workflow evaluations when changing a cross-surface user journey. They
define the observable outcomes and conceptual burden under review without
freezing the current UI.

See the [reference fixture ownership map](reference-fixtures.md) before adding
another full-laboratory example or journey.


Persistent-format changes follow the [prebaseline data policy](data-compatibility.md).
Current-format recovery remains tested; old development migration exercises do
not establish a compatibility baseline or require new readers.

## Handoff and evidence

The [platform index](platform-status.md) links current work owners. Each issue
body owns its current scope, remaining conditions and evidence; PRs own delivered
changes and validation. Architecture describes current contracts. Dated audits
and old numbered batches are historical, even when they use future tense.

A source merge, a release artifact, a consumer pin and human/hardware acceptance
are separate facts. Update affected issue bodies at closeout, not only comments.
Do not add a second handoff ledger or depend on ignored local notes.

Use tracked files and explicit source paths when exploring a checkout. Local
ignored directories can contain retired checkouts, bytecode, environments and
scientific data; their presence is not maintained implementation. Do not delete
them as part of a documentation or code refactor.

Tests generate .test-results and temporary runtime bindings; these are outputs,
not checkout prerequisites. A fresh source export plus locked dependency setup
should reproduce software checks. SDK installations and real-device settings
are explicit external prerequisites, never implied by a developer's machine.

For an unresolved failure, retain the source/environment identity, exact command,
observed outcome and diagnostic limits in its issue. Save a sanitized minimal
log/excerpt in that issue or a durable artifact with an access-controlled link.
CI artifacts have retention limits; a temporary path or expired artifact alone
is not a portable evidence record. If an old raw log is unavailable, say so and
retain its recorded observations without claiming to have reverified them.

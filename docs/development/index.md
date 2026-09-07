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

- [Repository map](repository-map.md)
- [Core workflow evaluations](workflow-evaluations.md)
- [Supervised laboratory pilot roadmap](lab-pilot-roadmap.md)
- [Pilot work slices and acceptance fixtures](pilot-work-slices.md)
- [Architecture](architecture/index.md)
- [Scalability benchmarks](scalability.md)
- [Project charter](project-charter.md)

Package inventories, generated-code rules, and implementation contracts stay in
package READMEs and docstrings beside the code that owns them.

Use the workflow evaluations when changing a cross-surface user journey. They
define the observable outcomes and conceptual burden under review without
freezing the current UI.

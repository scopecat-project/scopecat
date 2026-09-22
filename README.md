# Scopecat

<p align="center"><img src="assets/branding/app-icon.svg" alt="Scopecat app icon" width="160"></p>

Scopecat is a local-first Python toolkit for laboratory experiment workflows,
from direct instrument control and a first scan to sustained, large-scale
quantum experiments. It can live alongside notebooks and existing Python
projects. Workflows authored for Scopecat gain typed experiment structure,
bounded execution, live visibility, and durable results as they grow.

## Documentation

Start with the [documentation home](docs/index.md) or follow the
[source preview quickstart](docs/getting-started/quickstart.md) to create a
hardware-free project and complete the first durable run.

- [Runnable tutorial sandboxes](docs/tutorials/teaching-sandboxes.md)
- [Instrument control](docs/how-to/control-instruments.md)
- [Experiment authoring dataflow](docs/concepts/experiment-dataflow.md)
- [Chips and physical samples](docs/concepts/samples.md)
- [Measurement data](docs/how-to/use-measurement-data.md)
- [Python API reference](docs/reference/python/index.md)
- [Contributor guide](docs/development/index.md)

The [project charter](docs/development/project-charter.md) defines current
product priorities. Architecture documents describe present implementation
choices rather than product requirements.

## Source preview

Scopecat does not yet publish an end-user installation command. From a source
checkout, build the project console and create the hardware-free starter lab:

```sh
cd apps/scopecat-ui
pnpm install --frozen-lockfile
pnpm run build
cd ../..

uv run scopecat init ./my-lab
uv run scopecat config check ./my-lab
uv run scopecat start ./my-lab --static-dir apps/scopecat-ui/dist
uv run scopecat open ./my-lab
uv run python ./my-lab/notebooks/01_first_run.py
```

The generated configuration is ordinary version-controlled Python. The local
daemon owns immutable configuration history, run admission, resource ownership,
measurements, analysis, and durable results.

For a complete virtual lab, run:

```sh
uv run scopecat config check examples/reference_lab
uv run scopecat start examples/reference_lab --static-dir apps/scopecat-ui/dist
uv run scopecat open examples/reference_lab
uv run python examples/reference_lab/notebooks/30_drag_calibration.py
```

## Repository

- `packages/scopecat`: domain-neutral authoring, planning, execution, data, and
  notebook APIs.
- `packages/scopecat-server`: project CLI, daemon, services, and storage.
- `packages/scopecat-instruments`: typed capabilities, real SCPI drivers, and
  coupled virtual devices.
- `packages/scopecat-quantum`: hardware-independent quantum building blocks.
- `apps/scopecat-ui`: React/Vite project console.
- `examples/reference_lab`: retained integration fixtures; legacy author examples are being retired.
- `docs`: published user, extension, reference, and contributor documentation.

## Development

Development requires Python 3.14 or newer. Run the repository checks from the
root:

```sh
uv run pytest
uv run basedpyright
uv run lint-imports
uv run ruff check .
uv run ruff format --check .
uv run python scripts/check_document_links.py
uv run --group docs zensical build --strict
```

See the [contributor guide](docs/development/index.md) for focused package tests,
repository structure, architecture, UI development, and documentation preview.

# Scopecat

<p align="center"><img src="assets/branding/app-icon.svg" alt="Scopecat app icon" width="160"></p>

Scopecat is a local-first Python toolkit for laboratory experiment workflows,
from direct instrument control and a first scan to sustained, large-scale
quantum experiments. It can live alongside notebooks and existing Python
projects. Workflows authored for Scopecat gain typed experiment structure,
bounded execution, live visibility, and durable results as they grow.

## Documentation

Start with [download and installation](docs/getting-started/install.md), the
[documentation home](docs/index.md), or follow the
[first experiment](docs/getting-started/quickstart.md) through Help's
Parameters and scans Notebook to retain a first synthetic run.

- [Practice in the application](docs/tutorials/teaching-sandboxes.md)
- [Instrument control](docs/how-to/control-instruments.md)
- [Experiment authoring dataflow](docs/concepts/experiment-dataflow.md)
- [Chips and physical samples](docs/concepts/samples.md)
- [Measurement data](docs/how-to/use-measurement-data.md)
- [Python API reference](docs/reference/python/index.md)
- [Contributor guide](docs/development/index.md)

The [project charter](docs/development/project-charter.md) defines current
product priorities. Architecture documents describe present implementation
choices rather than product requirements.

## Desktop preview

The first numbered experimental release has not been published. The
[installation guide](docs/getting-started/install.md) identifies the historical
Mac/Windows preview and how to obtain a qualified candidate for current features.
It includes Python and application dependencies; opening saved data requires no
source checkout or laboratory SDK. Continue with [first steps](docs/getting-started/index.md).

## Source development

For daily source development, run from the public checkout with Python 3.14+, uv,
Node.js and pnpm:

```sh
uv run --locked python -m lab_tools.dev
```

This opens the native desktop with Vite HMR and the real backend, using a retained
worktree-specific home separate from installed application data. It starts blank;
create or register author source through Settings. Ctrl-C requests a safe exit
when work is idle. See [daily source development](docs/development/public-preview.md#daily-source-desktop)
for prerequisites, resource preparation and restart behavior.

For native packaging acceptance, use the
[local Mac packaging guide](docs/development/local-desktop-trial.md) with isolated
data. Daily experiment authors use their own Python and ordinary code folders
with the installed application.

## Repository

- `packages/scopecat`: domain-neutral authoring, planning, execution, data, and
  notebook APIs.
- `packages/scopecat-server`: project CLI, daemon, services, and storage.
- `packages/scopecat-instruments`: typed capabilities, real SCPI drivers, and
  coupled virtual devices.
- `packages/scopecat-quantum`: hardware-independent quantum building blocks.
- `apps/scopecat-ui`: React/Vite project console.
- `testing/reference_lab`: retained device, compiler, and scientific integration fixtures.
- `docs`: published user, extension, reference, and contributor documentation.

## Development

Use the [contributor guide](docs/development/index.md) for setup, focused checks,
repository structure and architecture. [Test feedback](docs/development/test-feedback.md)
explains CI and acceptance; documentation changes need link and strict build checks.

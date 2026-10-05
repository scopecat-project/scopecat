# Scopecat documentation

Start in the desktop application: open received data, use Help for a first lesson,
or continue your own Python/Notebook work. [Help lessons](tutorials/teaching-sandboxes.md)
include disposable peak practice and a persistent editable parameters Notebook.
They share the application; topics do not require separate services.

Scopecat is a local-first Python toolkit for laboratory experiment workflows.
It connects notebooks, typed experiment authoring, instrument control, live
visibility, and durable results. A lab can introduce it alongside existing
Python projects one workflow at a time; adopting Scopecat-managed execution may
still require rewriting an imperative workflow at its execution boundary.

This documentation follows the supported workflows and user concepts that the
project is trying to make simple. UI labels and source-checkout preparation may
change during internal iteration; the observable workflow outcomes are the
design contract under evaluation.

## Start here

Start with the [desktop first steps](getting-started/index.md). Open received data
directly, or follow the [first experiment](getting-started/quickstart.md) to create
ordinary code and retain a synthetic result. No per-project service installation
is required.

Use the [learning paths](getting-started/learning-path.md) to choose an author,
maintainer or extension route. Continue according to what you want to accomplish:

- [Learn through Help](tutorials/teaching-sandboxes.md) with peak practice or the
  parameters Notebook; saved code and parameter edits continue across sessions.
- [Control configured instruments](how-to/control-instruments.md) for direct and
  experiment-time device access.
- [Run from a notebook](how-to/managed-author-session.md) with parameter edits,
  scans and reopenable jobs.
- [Author experiments](concepts/experiment-dataflow.md) for point plans, compute
  placement, and durable results.
- [Track chips and physical samples](concepts/samples.md) for stable identity,
  run provenance, topology maps, and longitudinal analysis.
- [Use measurement data](how-to/use-measurement-data.md) for selection, Xarray,
  Arrow, pandas, Polars, and GUI projections.
- [Open and share data files](how-to/open-and-share-data.md) in the desktop app,
  compare records, or take a file to your own Python environment.
- [Share saved configuration](how-to/share-configuration.md) as independent local
  inputs, or [back up and restore](how-to/backup-and-restore.md) the same store/history.
- [Write ordinary Python analysis](guides/ordinary-analysis.md) with dataclass
  conclusions and retained source provenance.
- [Publish analysis](concepts/analysis-publication.md) for derived datasets,
  facts, artifacts, views, and parameter proposals.

## Reference

- [Command-line interface](reference/cli.md)
- [Project layout and manifest](reference/project-layout.md)
- [Python API](reference/python/index.md)

## Extend or develop Scopecat

Instrument and quantum integrations are product extensions and use public
Scopecat contracts. Start with the [instrument extension guide](extensions/instruments.md)
or [quantum extension guide](extensions/quantum.md).

Contributors changing Scopecat itself should use the
[development guide](development/index.md). Compiler, daemon, scalability, and
repository details live there so an experiment author does not need to learn
them before completing a first run.

The [project charter](development/project-charter.md) is the authority for
current product priorities and scope. The canonical
[user journey and entry map](development/architecture/public-application.md#user-journeys-and-entry-ownership)
connects the application, author tools and maintenance boundaries. These documents describe the present
system, not a compatibility promise or roadmap.

# Scopecat

Domain-neutral Python APIs for experiment authoring, execution, measurement and
retained analysis in the Scopecat application.

For a first experiment, follow the [Parameters and scans Notebook](../../docs/getting-started/quickstart.md)
in application Help. Its editable source and Notebook use the installed application's
data service. Continue in that folder for your own work, or create a separate
[author folder](../../docs/how-to/maintain-application.md#author-folders) in Settings.

- [Notebook author sessions](../../docs/how-to/managed-author-session.md) describe
  working with an already prepared laboratory source folder.
- [Parameter branches](../../docs/how-to/parameter-branches.md) cover saved parameter
  edits; [candidate verification](../../docs/how-to/verify-parameter-candidates.md)
  covers checking a proposed correction before publication.
- [Measurement data](../../docs/how-to/use-measurement-data.md) covers NumPy,
  Xarray, Arrow and finite projected reads. Install `scopecat[pandas]` or
  `scopecat[polars]` only for the corresponding conversion.
- [Authoring dataflow](../../docs/concepts/experiment-dataflow.md) explains scalar
  and array shapes, compute placement, results and recording.
- [Python API reference](../../docs/reference/python/scopecat.md) provides interface
  details.

Maintainers composing an independent local service can use the
[project layout and CLI](../../docs/reference/project-layout.md#independent-service-projects).
Framework contributors use the [development guide](../../docs/development/index.md).

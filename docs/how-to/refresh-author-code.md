# Refresh experiments, helpers and analysis

The reference lab enables author revisions by default. Edit the small files in
`src/reference_lab/workflows/authored`, then choose **Refresh author code** in
**Experiments**. Scopecat validates a complete source snapshot in a new process
before publishing it. Preview the updated controls and submit normally. The
instrument service stays running; admitted procedures retain their original code.
A helper-only change retains your control edits and scan while invalidating the
preview. Changed control declarations restore their defaults and retain named
form inputs for review; check the new controls before previewing again.

A syntax or import failure displays its file and traceback. The previous catalog
remains usable. Fix the file and refresh again. Refresh is explicit: saving a file
alone never changes the active definitions. Concurrent refreshes compare the
observed generation; a losing refresh must inspect the new state before retrying.

## Notebook path

Use the revision-aware connection when editing author files during a session:

```python
import scopecat as sc

project = sc.open_project(".")
with project.authoring() as authors:
    observed = authors.state()
    # Edit an experiment, imported helper, or analysis in your editor.
    refreshed = authors.refresh(expected_generation=observed.generation)
    launch = authors.prepare("signal", actor="alice")
    print(launch.preview.point_count, launch.preview.code_revision)
    submission = launch.submit(request_key="sample-a-signal-001")
```

Keep a request key for retries of the same submission. A preview retains the code
revision and configuration used to check it. Refreshing another revision cannot
replace the implementation inside that submission. The ordinary author still
writes existing `@sc.experiment`, `ControlSet` and `@sc.analysis_step` declarations;
there is no per-experiment service, procedure or catalog adapter to maintain.

`authors.prepare()` accepts the same typed `context`, `overrides`, `inputs`,
`control_edits` and `sample` fields as GUI launch. Pass a saved `ConfigContextRef`
and a tuple of `ParameterUpdate` values to explore another working point without
changing the laboratory default. The preview retains that context's exact sample
revision and overrides alongside its code revision; submission uses the same
shared resolver as GUI launch.

For retained analysis, explicitly choose the original revision from the run's
`author_code_revision` metadata or a newly refreshed revision:

```python
from scopecat.records.author_revision import AuthorRevisionRef

with project.connect() as lab, project.authoring() as authors:
    retained = lab.get_run(saved_run_id)
    original = AuthorRevisionRef(
        content_hash=retained.request.metadata["author_code_revision"]
    )
    result = authors.analyze(
        retained.id,
        "reference_lab.workflows.authored.signal:selected_mean",
        code_revision=original,
        key="original-model",
    )
    publication = retained.published_analysis(result.analysis_id)
```

This executes only analysis over retained data. Its publication records the
complete source revision as `author_code_revision`, in addition to the existing
analysis trace. Choosing the refreshed revision explicitly produces a separate
analysis using the new helper and analysis definitions. This endpoint supports
existing analysis declarations with default arguments; parameterized analysis
continues to use the ordinary Python analysis API in a deliberately selected
process.

`project.connect()` and directly imported Python functions keep the existing
single-code-root process contract. They do not hot-reload notebook modules.
Use `project.authoring()` for refreshes and new revision execution; use a fresh
process for direct imports of a different frozen code root. Ordinary direct
Python execution without a selected revision still records declaration identity,
not a claim of complete helper provenance.

## Maintainer configuration and supported boundary

The maintainer configures these paths once in `scopecat.toml`:

```toml
[authors]
source_roots = ["src", "config"]
refresh_roots = ["src/reference_lab/workflows/authored"]
```

`source_roots` archives the complete local dependency tree, including helper,
analysis and local resource files. `refresh_roots` identifies the subset ordinary
authors may change without restarting. These paths are separate from
`LabApplication(author_modules=(... ,))`, which selects discoverable experiment
modules using the existing decorator and controls. A project without `[authors]`
retains its existing initial-load behavior and has no refresh button.

Files outside the refresh roots form the maintained composition identity.
Compiler, driver, bootstrap, dependency declaration or other maintained source
changes require a matching maintainer restart; they cannot be smuggled into an
author refresh. All local Python dependencies must belong to declared source
roots. Symlinks, environments and caches are excluded. Arbitrary external source
installs, runtime-generated imports and live patches are outside the recovery
contract.

Revisions record Python and installed distribution versions. These are required
environment checks, **not a reproducible container or archived dependency
installation**. Retain matching installation artifacts for recovery. The state
root remains the live project; workers import from a separately materialized,
checksum-verified immutable code root. Historical dispatch reads the admitted
revision before importing any project code, then uses the existing exact
procedure-definition registry. Running workers are never replaced by refresh.
No automatic migration of arbitrary failed procedures is provided.

## Store and backup boundary

Author revisions require **project schema 64**. Opening a schema 63 store with
this runtime is rejected before modification. Keep the original store and its
pinned schema 63 Scopecat reader; start a separate schema 64 project for this
phase. There is no supplied 63-to-64 migration and no claim that version 64 can
read version 63 history directly. Consumers must preserve their existing project
and snapshot when upgrading the package.

Within schema 64, [backup and restore](backup-and-restore.md) retains the source
bundles, manifest identities, active generation and original admitted procedure
intents. Restored workers materialize the old code from the object store instead
of importing the currently edited helper. Restore the recorded external
environment and matching maintained source before resuming supported work.

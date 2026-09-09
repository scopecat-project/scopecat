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

## Declare simple form inputs

Ordinary Python parameters with defaults become form fields alongside controls.
For example, the reference `signal` experiment declares
`polarity: Literal["positive", "negative"] = "positive"`. Select its polarity in
Experiments, or pass `inputs={"polarity": "negative"}` to `authors.prepare()`.
The same declaration creates the catalog schema and validates input values before
binding the experiment. Its effective arguments, including defaults, are retained
in the admitted intent; a later refresh cannot replace those arguments or the
original implementation.

Use `str`, `int`, `float`, `bool`, or `Literal` choices of one scalar type for
these structural inputs. Discovery requires defaults and rejects nullable unions,
containers and other complex objects with a named error; maintainers can compose
those through the existing Python API. Values for controlled inputs belong in
`control_edits`, not `inputs`. Fixed and scanned controls keep their units, bounds
and existing compiler validation. There is no second per-experiment parser or
catalog to update when an ordinary author changes a supported parameter.

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

Author revisions require **project schema 65**. Opening a schema 63 store with
this runtime is rejected before modification. Keep the original store and its
pinned schema 63 Scopecat reader; start a separate schema 65 project for this
phase. There is no supplied 64-to-65 migration and no claim that version 65 can
read version 63 history directly. Consumers must preserve their existing project
and snapshot when upgrading the package.

Within schema 65, [backup and restore](backup-and-restore.md) retains the source
bundles, manifest identities, active generation and original admitted procedure
intents. Restored workers materialize the old code from the object store instead
of importing the currently edited helper. Restore the recorded external
environment and matching maintained source before resuming supported work.

## Catalog readiness and timeout evidence

A successful `scopecat start` or a running daemon status confirms API liveness,
not readiness of the author catalog. The first catalog request may validate the
source revision in a fresh process before discovering experiments. Launch calls
and source validation retain their existing 60-second deadlines. Because initial
validation is nested inside catalog loading, the outer catalog deadline can expire
first; validation may still be finishing when that response arrives.

Catalog, preview and submission timeouts identify the operation and the last
reported worker stage. A source-validation timeout reports whether the worker was
importing the framework, compiling source, importing the application or checking
source identity. If the worker did not reach its first marker, the stage remains
explicitly unknown. This attempt does not publish a revision. Existing retained
revisions are not replaced by a timed-out validation.

Inspect `.scopecat/daemon.log` before trying again. Timeout entries retain the
last reported stage and at most the last 8 KiB of worker stderr, marked when
truncated. Validation schedules one thread-stack dump after 30 seconds, without
locals, and cancels it on normal exit. The stack describes that earlier instant;
it is evidence for diagnosis, not proof of the final blocking cause. Ask the
project maintainer to check the indicated imports and matching environment.
After resolving the problem, explicitly refresh the author revision and request
a new preview. There is no automatic retry or fallback to different source.

A **submission timeout has an unknown outcome**: it may have been admitted before
the response was lost. Keep the original request key and use the existing
submission recovery to find that admission. Do not create a new submission to
resolve the timeout. Catalog loading and preview do not themselves submit an
acquisition.

These diagnostics do not establish why a particular cold import was slow or fix
cold-start latency. A later successful warm request is not evidence of reliable
cold startup; retain failed-attempt logs when evaluating a fresh installation.

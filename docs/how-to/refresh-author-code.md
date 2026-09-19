# Refresh experiments, helpers and analysis

For Notebook/IPython work, use `session = sc.notebook()` once. Saved edits are
selected for new experiment requests, both normal import styles work, and new
modules become available at the next cell. See the
[Notebook workspace guide](../tutorials/teaching-sandboxes.md#notebook-workspace-and-saved-edits).
The explicit operations below remain useful for scripts and controlled source selection.

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
alone never changes the active definitions. Refresh checks source imports and
declaration contracts without running experiment bodies. Preview checks the
program built for the selected inputs; a successful refresh alone is not a
validation of all structural choices. Concurrent refreshes compare the
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

Use the same connection/import cell on the first visit and after saving edits:

```python
session = project.authoring()
session.refresh()
from reference_lab.workflows.authored.signal import signal

request = signal()
launch = session.prepare(request)
```

`refresh()` publishes validated source and synchronizes the local author import
cache. Normal imports of module-level experiments then carry that admitted
revision. This also covers new experiment functions and new modules: save the file,
run `session.refresh()`, and import it normally. No `load_experiment` or manual
`importlib.reload` step is needed in this daily workflow.

Rerun the import line after a refresh to update its Python variable. Previously
bound aliases, requests and result instances keep their original definitions. A
request made before editing keeps its defaults and source even if prepared later.
To use the new definition, import it again and create a new request. An import
before refreshing cannot see a newly added file in the retained source snapshot.

For an existing experiment variable, `signal = session.refresh(signal)` remains
a single-expression alternative. `load_experiment` is an advanced exact-version
binding operation for recovery; it is not a required first-load step. Removed
keywords fail when constructing a new request, so update calling cells and editor
type errors when changing the signature or result dataclass.

Only project-local modules under `refresh_roots` are replaced. Imports use
checksum-verified archived bytes, including adjacent helpers and resources;
subsequent unsaved or saved workspace edits cannot leak into that import. Installed
packages, maintained composition and arbitrary Notebook state are not reloaded.
Restart the Notebook after updating these dependencies. Do not run concurrent
project imports while rebinding. Local Python calls that dynamically import a
module follow Python's current import table; use `authors.prepare(request)` for
revision-owned execution, rather than treating old Notebook objects as isolated
historical interpreters.

Syntax and server validation failures keep the previous active revision. If server
publication succeeds but the Notebook cannot import it, the exception identifies
the revision, local module bindings roll back, and no request is prepared. Correct
the local environment and refresh again (or use `load_experiment` for the exact
revision during advanced recovery); do not assume
server publication rolled back with the local import.

If waiting times out or the connection drops, the exception's `operation` retains
the preparation identity. Complete that operation and bind its exact result without
publishing again:

```python
state = interrupted.operation.reconnect(authors).wait(timeout=120)
signal = authors.load_experiment(signal, code_revision=state.active)
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

`project.connect()` keeps the existing single-code-root process contract.
Use `project.authoring()` and explicit typed rebinding for author edits; use a fresh
process for direct application loading from a different frozen code root. Ordinary direct
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

Preparation operations live alongside immutable revisions and experiment plans.
See [data migration](migrate-data.md) for the current storage boundary and tested
copy upgrades. Preserve original stores and pinned readers; opening a project
never implicitly migrates it.

Within schema 71, [backup and restore](backup-and-restore.md) retains source
bundles, manifest identities, active generation, preparation results and original
admitted procedure intents. Unfinished preparation records become `interrupted`
after daemon restart or restore; they do not silently rerun against today's files.
Restore the recorded external environment and matching maintained source before
resuming supported work.

## Long preparation and reconnecting

Source preparation has no implicit wall-clock deadline. A cold native-library
import can take longer on a new environment or slower computer. `authors.state()`,
`authors.prepare()` and synchronous `authors.refresh()` wait for initial source
preparation as needed. Waiting reports the last observed stage and operation ID;
GUI refresh displays its state and elapsed time and offers cancellation.

Use a handle when you want to control how long the notebook waits:

```python
from scopecat.daemon.preparation import AuthorPreparationTimeout

operation = authors.begin_refresh()
print(operation.id)  # Keep this ID to reconnect from another session.
try:
    refreshed = operation.wait(timeout=20)
except AuthorPreparationTimeout:
    print(operation.status())  # Preparation continues in the daemon.

# Later, including from a new authoring connection:
operation = authors.preparation(saved_operation_id)
refreshed = operation.wait()  # Same captured source; no resubmission.
```

`wait(timeout=...)` limits only the caller's wait. Ctrl+C also ends that wait
without cancelling preparation. A broken observation connection raises
`AuthorPreparationDisconnected`, retaining the same `.operation` handle; reconnect
to discover whether the daemon completed or restarted. Use `operation.cancel()` to request cancellation,
then inspect `status()` until it becomes terminal. `cancelling` means candidate
process cleanup is still pending. Cancellation before publication leaves the active
revision unchanged; cancellation after publication returns the existing success.
Publication and its success receipt commit in one transaction. The receipt means
source publication succeeded, even if subsequent worker adoption fails.

A submission transport error raises `AuthorPreparationSubmissionUncertain`, with
`.operation` and `.request`. Inspect the original operation ID first. If it is not
found, `authors.begin_refresh(operation_id=..., expected_generation=...)` can
resubmit that same request; an accepted ID always returns its original captured
revision and outcome. Do not create a new ID to resolve an unknown outcome.
Failed, cancelled and interrupted operations remain inspectable. After fixing the
cause, explicitly start a new refresh to capture the edited files.

A daemon health response confirms API liveness, not author catalog readiness.
The first GUI catalog request can still outlast its HTTP caller; inspect the
preparation shown in the refresh panel instead of assuming it failed. Short
launcher calls (catalog, preview and submission) retain their explicit 60-second
worker budget, starting after initial preparation. Historical worker restoration,
preview execution and acquisition/device deadlines are separate from source
preparation; they have not become unbounded by this change.

Inspect `.scopecat/daemon.log` for native-import diagnostics. Validation schedules
one thread-stack dump after 30 seconds, without locals, and cancels it on normal
exit. Failure evidence retains a bounded stderr tail. A reported stage or stack
is evidence of where the worker was observed, not proof of a deadlock or an
antivirus cause. No automatic retry or fallback to different source is performed.

An **experiment submission timeout has an unknown outcome**: it may have been
admitted before the response was lost. Keep its original request key and use
submission recovery to find that admission. Source preparation IDs and experiment
submission keys identify different operations. Catalog loading and preview do not
themselves submit an acquisition.

## Installed laboratory methods

A workspace can combine captured local experiments with maintained wheel packages:

```toml
[authors]
source_roots = ["src"]
refresh_roots = ["src/user_experiments"]

[authors.packages]
lab_methods = "scopecat-lab-methods"
```

The keys are top-level Python module names; the values are installed distribution
names. The application still explicitly registers discovery, for example
`LabApplication(author_modules=("lab_methods.experiments", "user_experiments"))`.
Analysis selection accepts modules in these declared packages or local refresh
roots. A package declaration does not automatically discover every experiment.
Use different top-level names for installed and local code to avoid shadowing.

Revisions retain content hashes of the declared module trees, including resources,
in addition to distribution versions. Added files and same-version replacements
change identity; generated bytecode does not. These are installed-content hashes,
not wheel archive hashes. Keep the matching wheels and environment for recovery.
Declare other laboratory packages containing imported helpers too: undeclared
third-party dependencies are still checked by Python/distribution versions, not
by their file contents. This is not a hermetic environment archive.

Only regular packages and single Python modules installed from wheels are
supported here. Editable distributions and namespace packages are rejected.
For maintainer exploration, keep editable code in the project's captured local
source roots; switch to a wheel for deployment. Do not modify installed files
while workers run: refresh is for local code only. Upgrading shared methods is an
environment operation requiring a restart and matching revision/deployment;
original analysis must restore the original installed package bytes. Existing
workers are not live filesystem integrity monitors.

## Execution dependency scope

Maintainers can select execution dependencies separately from notebook tooling:

```toml
[authors]
source_roots = ["src"]
refresh_roots = ["src/user_experiments"]
dependencies = ["scopecat-instruments", "scipy", "my-analysis[fit]"]

[authors.packages]
lab_methods = "scopecat-lab-methods"
```

Scopecat and its server are always included, as are declared installed author
packages. Scopecat follows their installed `Requires-Dist` metadata transitively,
evaluating platform/Python markers and requested extras. It checks requirements
against installed versions; it does not install or resolve a new environment.
Distribution names are normalized. Missing or incompatible dependencies fail
preparation with the dependency name. This does not inspect Python imports:
maintainers must declare dependencies used by local code, optional execution
paths and analyses, including extras. Package declarations still determine source
ownership; listing a dependency alone does not authorize importing its analyses.

The retained manifest records exact selected versions. Upgrading an unrelated
notebook-only package does not change the revision or block historical analysis.
Changing a selected package requires restarting the matching deployment, not
refreshing local author code. Root `pyproject.toml`, `uv.lock` and `requirements.txt`
are not automatically copied into scoped source revisions: they may describe
unrelated tooling. Keep deployment installation files and wheel artifacts
separately. Files explicitly inside source roots are still captured.

Omitting `dependencies` keeps the conservative full-environment inventory and
root installation files for exploratory projects whose dependency boundary is
not yet declared. An empty list explicitly selects just the framework, declared
author packages and their dependencies. New CLI starter projects select their
instrument dependency automatically. Additional installed packages are permitted
when recovering a revision, but every recorded version must still match. Older
schema 67 manifests keep their recorded full inventory; this change neither
rewrites their identity nor migrates an older store.

This remains an environment compatibility check, not a hermetic archive or an
import sandbox. Python must match; native libraries, drivers and external system
dependencies still belong to deployment qualification. Laboratory-owned imported
helpers need explicit package declarations to retain their content hashes.

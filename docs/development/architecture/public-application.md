# One application, independent execution contexts

Canonical product target, tracked by [#610](https://github.com/scopecat-project/scopecat/issues/610)
and [#671](https://github.com/scopecat-project/scopecat/issues/671).
This contract supersedes the first-run laboratory chooser and per-project/per-lesson
service topology. It is not a claim that the target is implemented. Native desktop
entry, explicit launch actions and recovery from interpreter mismatch and valid
stale process records are delivered foundations (#801–#804). The first product
batch (#812) delivers registered device ownership, canonical physical access,
independent setup/parameter editing and exact setup admission without a global
active setup. Same-service practice cleanup remains a separate delivery batch.

The product direction is stronger abstractions within one application, not a
collection of project services hidden behind one window. The concrete device,
connection, driver and setup boundaries are defined in
[device and driver management](device-management.md). Independent device ownership
replaces setup-owned connection copies; complete resolved snapshots remain evidence.

### Implemented boundary and the next software gate

Explicit source registration now covers GUI, Python, workers, retained plans and
the optional Notebook launcher. Moving an author source does not move application
process ownership or recreate its old location on restart. A service without an
available author source can still expose retained data.

For independent parameter requests, each draft selects an exact maintained setup
revision. Another draft's selection or a changed application default does not
invalidate that reference. Submission checks the maintained revision, freezes its
evidence and uses shared resource claims. The browser journey covers distinct
saved setups, task/record reopening and restart without duplicate acquisition.
The server journey checks alias contention using the same maintained physical
access key and rejection before acquisition.

Direct-instrument listing, connection and release also require an exact maintained
setup. They work without global activation. Each Instruments page retains its own
selection while connected. Device maintenance edits the application device registry;
setups reference stable devices and freeze exact connection/driver revisions for work.
Open retries check the original setup and retain the original acquisition. Aliases
sharing a maintained physical access key use the same claims across contexts.

The application entry now owns one fixed runtime root under its installation home.
Author folders register with that owner and do not create endpoints or installations.
The native window loads the workbench directly and offers explicit stop/background
choices when closing. An empty application can register independent author folders
without requiring an installed capability package. Captured sources retain their
bound composition even when the live application changes.

Candidate environments and capability declarations are qualified before selection,
including driver metadata and content identity without device connection. Selection
requires stopped process ownership, updates source interpreter bindings under the
same start fence, and retains an interrupted selection for explicit retry. This is
the application maintenance boundary: software/settings actions live in the
workbench, and the separate manager and per-directory service registry are removed.
Help now starts an owned software practice in this service. Ordinary selected data
and practice use the same preview, retained-reference protection, durable cleanup
receipt and physical-file reclamation. Practice adds software-only admission and
an explicit choice to preserve or discard edited files. The former per-lesson
environment launcher and directory-cleanup command are removed.

## Installation and everyday use

The target is a platform-standard public application installation, update and
uninstall. Python environments, GUI assets and worker processes are application
implementation details. Uninstalling software must not delete scientific data.
Launch opens the workbench directly, restoring the last task or history; first use
shows useful experiment and tutorial actions there. It never requires choosing a
laboratory, service or device context first. Missing capabilities or unresolved
physical choices are explained at the affected action, with inline setup/recovery.
Opening the application, history or code must not initialize devices or replay work.
Preparing, connecting and acquiring have explicit boundaries and observable results.

One default local application service serves GUI, VS Code/Python and CLI clients.
VS Code opens an ordinary code folder; JupyterLab is optional. Code folders and
device contexts do not create separate application services, ports or installations.
Qualified workers may use different processes; one service does not mean one Python
process. Heterogeneous runtime support is not a prerequisite for this first target.

The old host's service registration and setup UI are retirement targets, not an
alternative product entry. Its remaining teaching/installation consumers must be
replaced together with their ownership assertions; see
[application host](application-host.md).

## Ownership without a laboratory container

“Laboratory” may describe an organization or a capability package, but is not a
required aggregate that owns code, data, devices, process and user navigation.

| Responsibility | Owner |
| --- | --- |
| Installation, application endpoint, lifecycle and recovery | Local application runtime |
| Editable code and immutable submission revision | Explicit registered author source |
| Stable device identity and versioned connections | Application device registry |
| Driver discovery, qualification and controlled replacement | Application capability management |
| Logical roles, channels, routes, capabilities and constraints | Revisioned device resolution context, referencing maintained setup |
| Resolved physical identity, admission, claims and release | Application-wide resource authority |
| Sample/target, parameters, accepted calibration and publication | Existing scientific models and explicit task/session references |
| Records and source/parameter evidence | Persistent data space, independent of code location |
| Disposable tutorial work | Owned practice scope within the application service |

A device resolution context is an input to preparation/execution, not another
service or a second copy of the setup catalog. It must not own a data root, source
root, global sample or instrument process. Reuse maintained setup revisions for
roles, routes and execution policy, referencing registered devices rather than
maintaining independent copies of their connection settings. The current embedded
instrument registry is transitional; it must not remain a competing device writer.

Each page, kernel and draft retains its own explicit context references. Restored
tasks retain their recorded references; there is no application-wide mutable current
configuration. Resolve ambiguity inline before physical execution, never by selecting
the first device. Changing a context affects future requests only, and cannot
reconfigure an instrument or mutate a submitted task. Freeze the resolved setup,
source and parameter evidence at the existing submission boundary.

Resource claims use canonical physical identities across contexts and author sources.
Two names for the same instrument do not create two owners. Conflicting operations
must queue or fail with a useful explanation before hardware side effects. Switching
incompatible runtime/device state requires quiescence and explicit preparation;
serializing submissions alone does not establish equivalent physical state.

## Data lifecycle and tutorial cleanup

Data cleanup is an application capability for ordinary work as well as tutorials.
Practice ownership supplies a convenient selection and a software-only execution
policy; it must not own a second deletion engine. Neither an executable setup nor
a research association implies ownership of all referenced scientific records.

The common cleanup flow previews exact records, related tasks, retained scientific
dependencies and reclaimable files. Hiding a record does not reclaim storage;
exporting or backing it up does not implicitly authorize deletion. A saved analysis,
adopted calibration or published parameter revision must not silently lose its
evidence when a source measurement is selected. Explain the retaining dependency
and require an explicit, scientifically valid selection instead of cascading to it.

Execution rechecks that preview, fences new writes/references, settles affected
tasks and persists cleanup progress for retry. Ordinary hardware work follows its
normal cancellation and device-release protocol; the software worker termination
policy of a practice is not permission to terminate hardware workers. Shared
content is reclaimed only after both retained references and in-flight publications
are accounted for. Report record removal and file reclamation separately when one
has completed and the other needs retry. Independent work stays usable.

Expose ordinary cleanup beside selected data, with storage usage and unfinished
operations in Data; Help offers the same operation preselected to one practice.
The delivery gate includes ordinary unreferenced-data cleanup, explanation of
retained calibration/analysis evidence, interrupted cleanup recovery and isolation
from unrelated drafts and measurements. This is a target contract, not a claim
that extracting a common SQL deletion function completes data lifecycle support.

Help opens a prepared task in the same workbench and application service. Each
practice scope owns its parameter/record namespace, tasks and worker lifecycle.
The server grants simulation-only backend capabilities to practice execution;
a client-side label or context name cannot authorize physical devices. This is an
execution-capability boundary, not an OS sandbox for arbitrary user Python.

Reset/clear fences new work, cancels or drains owned tasks, joins workers, releases
resources and rejects late writes before deleting app-owned practice records and
artifacts. Failure remains recoverable and visible; do not report cleanup complete
while workers still write. User-edited/exported files require an explicit preserve,
export or discard choice. Real tasks, records, devices and other practice scopes
are unaffected. A lesson does not require its own service or virtual environment.

## Retiring the manager and legacy source path

Remove the standalone manager product surface. Move task failures and recovery into
the workbench, device connections and driver maintenance into **Devices and drivers**,
application updates into settings, storage into **Data**,
and diagnostics/tutorials into Help. Hiding the old chooser or renaming a laboratory
does not satisfy this contract. Retain necessary logs and controlled lifecycle actions.
Devices and drivers is a direct workbench destination, reachable from the
affected experiment action; it is not an obligatory maintenance gateway.
Application exit must explain active work and offer an explicit stop or background
choice; failed shutdown remains actionable without Task Manager. Never kill a process
on PID alone, replay interrupted tasks or take over an unrelated development home.

The reserved `legacy` service-source identity was removed in #806. The remaining
work is independent application startup and retirement of project-service topology,
not another source rename. Every author source uses the same explicit registration,
publication and execution contract. Keep the execution service itself.
This is not a rename to `default`, an old-format reader or a data-directory deletion.

## Standard composition

Public owns application construction, lifecycle, capability discovery, admission,
results and error reporting. Laboratory maintainers supply experiment modules,
procedures, calibration/publication registrations and specialized execution or
driver providers. Ordinary experiment folders should not need a copied
`create_application` callback to assemble public registries.

The first slice uses typed `[lab.capabilities]` declarations in the existing source
manifest. Public resolves them only when loading execution code, under its exact
workspace/revision identity. Bootstrap remains lightweight; instrument backends
remain separately loaded in their owning process. A custom application is an
explicit alternative, never combined with declarations by hidden precedence.
An independently installed adapter can instead own this composition in a package
resource. First-run connects a prepared author directory and its environment, with
an optional explicit local settings JSON file. An optional fixed delivery installs its locked wheels offline; setup does not
resolve a new dependency graph.

Private should progressively become laboratory capability packages plus editable
experiment code and local machine settings. Vendor SDK locations, device addresses
and deployment authority must not be duplicated merely by copying experiments.
Source checkouts of public remain a framework development option, not a deployment
prerequisite. Required dependency versions and supported capability boundaries must
be explicit before independent environments are admitted.

## Installed laboratory adapters

A prepared author directory may select one installed distribution-owned manifest:

```toml
[lab.adapter]
distribution = "example-lab"
manifest = "example_lab/adapter.toml"

[lab.capabilities]
author_modules = ["my_experiments"]

[authors]
source_roots = ["src"]
refresh_roots = ["src"]
dependencies = []
```

The adapter resource declares its bootstrap, instrument backend and shared
capabilities, plus `[authors.packages]` mapping its implementation modules and
shared laboratory dependencies to installed distributions. The project can add
local author modules; it cannot override adapter singletons or collection providers.
Discovery reads distribution metadata and owned files without importing adapter
code. Execution resolves each adapter symbol from its declared installed module.
Editable installs are rejected: laboratory implementation updates use a rebuilt
wheel, while ordinary experiment edits use source refresh.

Installed code and package resources use the existing installed-author content
identity. A same-version file change is still a changed implementation. Revision
workers validate that identity before resolving current package declarations.
Captured local source remains available; historical adapter wheels must be retained
and restored separately. Recording identity does not archive or reinstall wheels.

Registration probes the selected experiment interpreter, not the host interpreter,
and records adapter content identity. Startup checks it; changing the adapter
requires stopping and rechecking. Status and stop parse only the local manifest
and runtime binding, so removal of an adapter cannot disable service recovery.
Laboratory maintainers can build that fixed artifact through a TOML delivery
recipe selecting the reviewed lock project, group and local packages. This reuses
the public builder, inventory/hash checks and installer rather than introducing
an adapter-specific installation script. First-run uses an installer-owned attempt
record, process identity and locks to retain/retry only its own incomplete `.venv`.
The created environment stays at its final path; the retained artifact owns its GUI.

This is a same-runtime package contract, not shared device authority across
heterogeneous environments or a native installation system.

## Local settings boundary

`runtime.settings_file` selects optional adapter-owned JSON. Public provides
`read_lab_settings(project_root, SettingsModel)` for typed, single-read bootstrap
settings and records path/content identity at host registration. Startup checks
that identity; a stopped service can accept changes through explicit recheck.
Stop never requires reading the settings file. Registration validates JSON without
importing adapter code; adapter field validation occurs at bootstrap loading.

The private consumer now selects its initial configuration recipe through this
file instead of an ambient shell profile. Persisted scientific configuration
remains authoritative for existing stores. SDK location, provider/worker injection
and runtime qualification are still separate work: they have not been moved into
this initial settings contract. Keep local settings outside captured source roots;
this API does not automatically redact files placed in source directories.

## Working copies and version ownership

Remember a preferred experiment folder without making it the service owner; allow additional working copies for
stable/experimental code, separate authors and alternative analysis. A page or
kernel binds one code source. Shared libraries are declared dependencies, not
implicit imports from whichever other folders are open.

| Content | Owner and update boundary |
|---|---|
| Public runtime | Application installation and controlled updates |
| Drivers, vendor SDK and laboratory compiler capabilities | Qualified laboratory runtime; changing experiment source does not replace them |
| Experiment/procedure/analysis code | Editable working copy, frozen to exact source on submission |
| Parameter declarations and access code | Explicit required structure, types, units and semantics; compatibility is not inferred from matching table names |
| Values and accepted calibration | Versioned working-point state; experimental branches have separate mutable heads |
| Executable setup | Maintained roles/routes, registered device references and execution policy |

These concepts may live in one repository. Independent ownership does not require
one repository per concept. Each admitted run must retain its resolved combination;
folder paths are locations, not evidence of compatibility or hardware ownership.

A trial code copy may read a stable parameter version. Trial publication should
advance an explicitly selected trial working-point branch, not silently update the
shared accepted head. Changed table contracts need an explicit adaptation before
shared use. Independent driver versions may require different processes, but one
physical device cannot acquire a second owner through another code folder. Switching
qualified environments requires quiescence and explicit device reinitialization
where necessary. Serial runs alone do not establish equivalent hardware state.

Current same-environment workspace publication and independent parameter heads
provide building blocks. Heterogeneous environments, parameter-contract negotiation
and application-wide device authority remain unimplemented contracts;
see [workspace bindings](workspace-bindings.md) and
[configuration ownership](configuration-ownership.md).

## Ordered implementation and evidence

1. Establish device/connection ownership, setup references and unified physical
   access, including probes and resident connections. Build on explicit sources and
   contexts from #806/#808; replace embedded connection editing, not merely the
   service chooser. Follow [device management](device-management.md) under #671/#754.
2. Enter the workbench directly and replace manager consumers with contextual
   actions/settings (#675, #615). Source edits use refresh; runtime updates preserve
   content identity, data and recoverable installation state (#712).
3. Run and clear tutorials as owned simulation-only practice scopes (#565).
4. Qualify the combined software journey (#616): launch directly; create two drafts
   with independent simulated device contexts on one service; complete tutorial
   manual peak selection; clear it without changing either draft or retained real
   records; restart and reopen retained tasks without replay. Test conflicting
   aliases for one physical resource before admitting real hardware. Verify GUI and
   Python clients share authority and recovery does not require killing processes.
5. Only then perform ordinary-user and Windows physical-device qualification of
   scan → manual peak selection → fine scan → verification → publication → reopen.
   Simulation, platform usability and scientific validity are separate evidence.

Preserve current-format evidence and recovery. No development-store migration
chains, supported data baseline or automatic replay are introduced by this work.
Software UX convergence is the current P0, before physical acceptance. It can be
developed on Mac; Windows uses the same product flow plus platform/hardware checks.
Do not make this depend on a general workflow editor, remote service, tray integration,
arbitrary dependency isolation or a complete physical-state model. Keep targeted
scientific, process-ownership and persistence tests; do not preserve retired service
topology merely to keep its old journeys unchanged.

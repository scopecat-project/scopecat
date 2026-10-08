# One application, independent execution contexts

Canonical product target, tracked by [#610](https://github.com/scopecat-project/scopecat/issues/610)
and [#671](https://github.com/scopecat-project/scopecat/issues/671).
The [desktop product gate](desktop-product.md) refines the presentation and runtime
direction: multiple independent windows, data-only use without execution startup,
and no commitment to the prototype GUI, shell or daemon/HTTP topology. References
to one service below express shared resource authority, not a fixed process layout.
This contract supersedes the first-run laboratory chooser and per-project/per-lesson
service topology. Those paths have retired, and shared device ownership and
same-application practice are delivered. Desktop PR #829 provides ready-to-run
installation, #837 independent data and bounded Mac/Windows interaction, and #844
ordinary source with independent execution and SDK environments. PR #845 qualified
composition, publication and remaining retirement; physical scientific acceptance
and unfamiliar-user observation remain separate.

The product direction is stronger abstractions within one application, not a
collection of project services hidden behind one window. The concrete device,
connection, driver and setup boundaries are defined in
[device and driver management](device-management.md). Independent device ownership
replaces setup-owned connection copies; complete resolved snapshots remain evidence.

## User journeys and entry ownership

The desktop application is the everyday center for ordinary users: one application,
one data space and multiple editable author folders. Windows is the primary user
platform; macOS remains a supported public desktop with the same product flow.
Notebook/Python and the SDK own author work, while the application owns durable
work, results and shared devices. Help provides teaching with minimal preparation
forms. Settings and device maintenance appear when needed, not as an admission
checklist. CLI commands remain optional maintenance and automation tools. Current native
installation does not put a console on global PATH; the wheel's console scripts
are not a completed desktop CLI contract. The confirmed target is a native
application with its own runtime and persistent data, supplying an optional
CLI/launcher that controls or connects to the same application without silently
creating another data service. Multiple author venvs contain SDK/client and
experiment/device dependencies, not the complete GUI application; deleting a venv
must not delete experimental data. The application-owned CLI is not yet implemented.
Alternative pip GUI, independent CLI-client and portable/headless distribution are
not current second primary channels; revisit them only for explicit needs. The
[installation identity boundary](../installation-layout.md#application-command-and-data-identities)
distinguishes distribution, installed copy, process owner and persistent data.

The runtime can isolate multiple application homes for development, acceptance or
separate deployments. This does not introduce a multiple-space manager, per-topic
services or cross-home physical-device exclusion. Current local commands accept
loopback endpoints; LAN authorization and unattended remote service operation are
not delivered product promises. See [application host](application-host.md).

The table maps current entries to their owners and limits. It is not a claim that
every source change below has reached an installed release; use
[platform status](../platform-status.md) for delivery and evidence identities.

| User journey | Default entry | Owned code or data | Present capability and remaining boundary |
| --- | --- | --- | --- |
| Receive and inspect data | Desktop **File → Open**, then Data | Portable scientific evidence and imported records; each window owns its selection | [Open/share recorded data](../../how-to/open-and-share-data.md) and independent Python reading work without the original author environment or devices; a capture is not a whole-store backup. |
| Learn | **Help → Start peak practice** or **Learn with Notebooks** | Practice owns disposable records/notes; all seven topics each retain an ordinary author folder, teaching setup and parameter branch | [Help lessons](../../tutorials/teaching-sandboxes.md) cover manual decisions and all seven Notebook topics through shared preparation and continuation (#913). Course design, actual editor and unfamiliar-user observations remain separate. |
| Write code | Continue the Help Notebook in VS Code; **Settings → Author code** for new/existing ordinary folders | User-owned Python/Notebooks, local SDK and declared dependencies; retained source revisions at submission | [First experiment](../../getting-started/quickstart.md) and [author sessions](../../how-to/managed-author-session.md) use independent environments. Multiple folders share application data, not each other's implicit imports. Older teaching generators still need convergence. |
| Run, inspect and analyze | Notebook/Python or **Experiments**; **Runs**, **Analyses** and **Decisions** for retained work | Explicit source, setup and parameter references; application-owned tasks/results; author-owned analysis | Preview and explicit submission are distinct. Reopening history does not acquire again. Default entry is Runs history, not persisted last-viewed-task restoration; see #675. |
| Share configuration or source | **Configuration → Share and import configuration**; ordinary source files/version control for code collaboration | Saved parameter definitions/values, optional setup and retained source; originals and derivation receipts | [Configuration sharing](../../how-to/share-configuration.md) creates editable local inputs. Source acceptance is inert; trust, dependencies, registration and local device binding remain explicit. General graphical authoring is still a proposal. |
| Back up or change computer | Optional stopped-project snapshot commands and [restore guidance](../../how-to/backup-and-restore.md) | Current-format store/history and captured local files; external source/dependency artifacts retained separately | Restore preserves store identity, not an independently writable clone. No one-click whole-computer backup, cross-version baseline or automatic environment relocation is promised. |
| Maintain or automate | Contextual **Devices and drivers**, **Settings**, and optional [CLI](../../reference/cli.md) | Installation, registered devices, execution/SDK environments and explicit automation declarations | Controlled updates and work-aware Quit exist. Maintenance is not required to open received data or start Help practice; physical qualification remains separate. |

### Current navigation and proposed hierarchy

`apps/scopecat-ui/src/App.tsx` currently renders eleven peer destinations:
Experiments, Data, Samples, Runs, Analyses, Decisions, Reviews, Devices and drivers,
Configuration, Settings and Help. Native File/Open and window commands, external
Notebook/Python, SDK and CLI are additional entry surfaces, not more data owners.
The [repository map](../repository-map.md#entries-processes-and-delivery) locates
these implementations and the retained teaching/server-only callers.

A proposed information hierarchy puts running/browsing/analysis in everyday work,
keeps Samples, Configuration, Decisions and Reviews reachable from their relevant
scientific context, and treats Settings/device maintenance as supporting actions.
Help remains a direct learning entry. This hierarchy is **not implemented** and
does not prescribe a new order, hide capabilities or rename current GUI labels.
Any later GUI change must demonstrate the journeys above before replacing entries.

### Bounded follow-up order

1. **Teaching/source convergence (#565):** inventory the remaining generated
   teaching consumers, carry editable scientific definitions and explanatory
   Notebooks into ordinary author work, and update their callers together. The
   seven supplied topics share Help preparation and continuation (#913); course
   ordering, difficulty and grouping remain teaching-design work.
   Preserve existing teaching/VS Code tasks until replacement journeys work.
2. **Entry hierarchy:** evaluate a bounded navigation proposal against receiving
   data, learning, authoring and returning to results. Measure whether users can
   find the next action before committing to GUI changes. #675's bounded audit
   found no remaining software gap; its closure decision does not authorize this
   redesign or require last-task restoration.
3. **Sharing and recovery guidance (#502/#574):** keep independent configuration
   derivation distinct from same-store recovery, identify any concrete usability
   gap after the merged installed-sharing journey, and retain external dependency
   requirements. Do not add another archive format or compatibility promise.

Actual editor, unfamiliar-user and supervised device observations remain #616.
Use the [existing work owners](../platform-status.md#remaining-work-owners); this
ordering is a limited proposal for subsequent work, not another implementation
batch, new issues or approval to change product behavior in this documentation PR.

## Recoverable editing

Valuable edits must survive navigation, new windows and restart. Automatic draft
persistence does not save a parameter version, publish/apply changes or submit an
experiment. Changed baselines require revalidation while retaining input; concurrent
edits must not silently overwrite each other. This approved target is not fully
implemented. The [recovery and native storage contract](draft-recovery.md) separates
application-owned drafts from the independently required native store repair.

## Implemented application boundary

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
The native window loads the workbench directly. Window closing, tray restoration
and explicit whole-application Quit follow the delivered
[desktop lifecycle contract](desktop-lifecycle.md).
An empty application can register independent author folders
without requiring an installed capability package. Captured sources retain their
bound composition even when the live application changes.

Application dependencies are built into the native package; updating replaces that
package after work has stopped. Author dependency preparation and driver selection
are explicit, independent maintenance actions, with metadata and content checks
before activation. There is no user-facing candidate application environment switch.
Software/settings actions live in the workbench; the separate manager and
per-directory service registry are removed.
Help now starts an owned software practice in this service. Ordinary selected data
and practice use the same preview, retained-reference protection, durable cleanup
receipt and physical-file reclamation. Practice adds software-only admission and
an explicit choice to preserve or discard edited files. The former per-lesson
environment launcher and directory-cleanup command are removed.

Help's seven Notebook topic entries prepare persistent ordinary author work through
the same registration, local SDK and execution-environment operations as Settings.
Each new directory owns a unique setup/parameter namespace. Continue preserves
source and environment and never runs cells. This is not a resettable practice
scope: the supplied code is device-free, but edited Python retains ordinary author
permissions. Records use ordinary Data cleanup; source files remain user-owned.

## Installation and everyday use

The target is a platform-standard public application installation, update and
uninstall. Python environments, GUI assets and worker processes are application
implementation details. Uninstalling software must not delete scientific data.
Launch opens the workbench directly into Runs history, with experiment and Help
actions available. The #675 audit distinguishes this from persisted restoration
of the last-viewed task; no such restoration is claimed. It never requires choosing a
laboratory, service or device context first. Missing capabilities or unresolved
physical choices are explained at the affected action, with inline setup/recovery.
Opening the application, history or code must not initialize devices or replay work.
Preparing, connecting and acquiring have explicit boundaries and observable results.

One default local application service serves GUI, VS Code/Python and CLI clients.
VS Code opens an ordinary code folder; JupyterLab is optional. Code folders and
device contexts do not create separate application services, ports or installations.
Qualified workers may use different processes; one service does not mean one Python
process. Heterogeneous runtime support is not a prerequisite for this first target.

The manager and per-source service product topology are retired. Retained
teaching/installation callers must be replaced together with their ownership
assertions rather than promoted to another product entry; see
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
| Disposable peak practice | Owned practice scope within the application service |
| Persistent parameters lesson | Ordinary editable author folder and independent scientific namespace |

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
source/environment maintenance into settings, storage into **Data**,
and diagnostics/tutorials into Help. Hiding the old chooser or renaming a laboratory
does not satisfy this contract. Retain necessary logs and controlled lifecycle actions.
Devices and drivers is a direct workbench destination, reachable from the
affected experiment action; it is not an obligatory maintenance gateway.
Application exit must explain active work and offer an explicit stop or background
choice; failed shutdown remains actionable without Task Manager. Never kill a process
on PID alone, replay interrupted tasks or take over an unrelated development home.

The reserved `legacy` service-source identity was removed in #806. Independent
application startup and retirement of project-service topology are delivered by
#829, #837, #844 and #845. Every author source uses the same explicit registration,
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

Registration probes the selected experiment interpreter, not application Python,
and captures package identity. The author source owns this declaration; the
application CLI no longer accepts a distribution/manifest injection. Changing the
package or execution interpreter requires refreshing the source and explicitly
updating an idle driver. It does not replace the application. Retained work still
requires its recorded source/environment identity; an unavailable extension cannot
prevent opening unrelated data or stopping the application.

An extension wheel is optional distribution of ordinary laboratory code. Install
it in the selected execution environment, never the desktop runtime. Vendor SDKs
can use their own process/environment while device ownership remains unified.
Offline environment bundles are separate from the public native installer.

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

Independent execution/SDK environments and application-wide device authority are
implemented. General parameter-contract negotiation is not promised by these
boundaries; see [workspace bindings](workspace-bindings.md) and
[configuration ownership](configuration-ownership.md).

## Delivery and evidence

Device/connection ownership, direct entry, contextual maintenance and same-service
practice were delivered before the desktop milestone; their old implementation
sequence is not a pending backlog. The desktop milestone consists of #829
(installation), #837 (data and desktop), #844 (ordinary source and environments),
and #845 (composition/release/retirement), all merged. Remaining observations
are tracked by #616; application convergence #671 is closed.

The [packaging matrix](desktop-packaging.md#composition-evidence) maps composition
checks to maintained tests and prior evidence. Preserve independent contexts,
resource exclusion, source/environment identity, manual continuation, cleanup and
restart without replay when retiring an old entry or fixture. Ordinary-user
observation and Windows physical-device qualification of scan → manual peak → fine
scan → verification → publication → reopen remain separate from software evidence.

Preserve current-format evidence and recovery. No development-store migration
chains, supported data baseline or automatic replay are introduced by this work.
Software UX convergence is the current P0, before physical acceptance. It can be
developed on Mac; Windows uses the same product flow plus platform/hardware checks.
Do not make this depend on a general workflow editor, remote service,
arbitrary dependency isolation or a complete physical-state model. Keep targeted
scientific, process-ownership and persistence tests; do not preserve retired service
topology merely to keep its old journeys unchanged.

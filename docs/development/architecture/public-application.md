# Public application and laboratory capabilities

Product direction after the installed Windows trial, tracked in
[#671](https://github.com/scopecat-project/scopecat/issues/671).
The successful installation of a teaching/service manager did not establish the
intended experiment application. This page distinguishes the next product contract
from the already implemented ownership primitives.

## Installation and everyday use

The target is a platform-standard public application installation, update and
uninstall. Python environments, GUI assets and worker processes are application
implementation details. Uninstalling software must not delete scientific data.
First use should create an initial experiment code folder or connect a laboratory
adapter and settings, choose a primary code folder, and offer a separate data
location with a useful default. Completing setup opens the experiment workbench.

Normal launch restores the primary workbench. Management belongs under settings
and recovery; teaching belongs under Help. Reopening a context never replays a
measurement. Starting an explicitly selected service still follows its device
initialization policy; this is distinct from submission. Notebook connections
identify their code folder and share the application's execution authority.

The first-run UI now creates an ordinary source project or connects a prepared
laboratory code directory and enters its workbench. It can place new data separately
and reuses a local `.venv` or the application environment. Native installers,
automatic adapter-package installation and dependency/environment preparation
remain future work; connecting a source directory does not implement those contracts.

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
an optional explicit local settings JSON file. It does not install packages or
resolve an environment.

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

Default to one primary experiment folder; allow additional working copies for
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
| Executable setup | Maintained routes/device declarations and current execution authority |

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
and consolidated cross-service device authority remain unimplemented contracts;
see [workspace bindings](workspace-bindings.md) and
[configuration ownership](configuration-ownership.md).

## Ordered implementation and evidence

1. Standard capabilities with public starter/teaching consumers and a real private
   laboratory consumer; preserve lazy imports and frozen execution provenance.
2. Direct primary-workbench entry, followed by a first-run laboratory connection
   flow and explicit data/code/settings locations.
3. A stable and experimental working-copy journey that checks code revisions,
   parameter-contract compatibility and independent parameter publication.
4. Shared device authority and controlled runtime switching; native platform
   installation/update/uninstall qualification.

Preserve current-format evidence and recovery. No development-store migration
chains, supported data baseline or automatic replay are introduced by this work.
The next human trial should install public, connect a laboratory, open an existing
experiment and try a second code copy without unintentionally changing the stable
code or shared parameters. It should not substitute tutorial completion for that
product journey.

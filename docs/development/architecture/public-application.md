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

Native installers and this first-run connection UI are not implemented by the
initial capability/entry slices. Their acceptance must demonstrate an ordinary
laboratory experiment, not only a teaching exercise.

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
This is an initial standard composition contract, not yet an installable adapter
package manifest, dependency installer or OOBE.

Private should progressively become laboratory capability packages plus editable
experiment code and local machine settings. Vendor SDK locations, device addresses
and deployment authority must not be duplicated merely by copying experiments.
Source checkouts of public remain a framework development option, not a deployment
prerequisite. Required dependency versions and supported capability boundaries must
be explicit before independent environments are admitted.

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

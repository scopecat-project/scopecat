# Device and driver ownership in one application

This is the target contract, not an implemented feature inventory. It refines
[the application model](public-application.md) and
[configuration ownership](configuration-ownership.md). A single application needs
independent device, driver, source and task abstractions; collecting project
services behind one window does not establish those abstractions.
Device registration and access convergence are tracked by
[#809](https://github.com/scopecat-project/scopecat/issues/809); driver qualification
and controlled replacement are tracked by
[#712](https://github.com/scopecat-project/scopecat/issues/712).

## Everyday operations

**Devices and drivers** is an optional workbench destination, not a separate
manager or a prerequisite selection screen. Experiments open directly. Missing
devices and capabilities link to the relevant action and return to the draft.

The device list shows meaningful names, connection/maintenance state and current
owners. Selecting a row reads retained information; it does not connect hardware.
Adding a device accepts a connection and a compatible installed driver. Explicit
**Test connection** obtains permission through the same resource authority as an
experiment, then displays identity evidence, capabilities and a diagnostic result.
An unreachable device can still be saved as unverified; it is not reported ready.

An experiment binds roles such as readout or bias to registered devices and
channels. A unique valid binding can be shown directly; unresolved alternatives
are confirmed inline. Selection never configures the device. A busy device shows
its owning task or manual session, with actions to inspect, wait or request normal
termination. An uncertain operation remains actionable as device attention.

Changing an address belongs in the device detail. Changing wiring or role/channel
assignments belongs in setup. Changing scan power belongs in experiment inputs.
Changing a driver implementation belongs in driver maintenance. None of these
requires creating or selecting another service.

## Owners and retained references

| Object | Owns | Does not own |
| --- | --- | --- |
| Registered device | Stable identity, display information, identification evidence and maintenance state | Experiment source, parameters or a daemon |
| Device connection revision | Transport settings, selected qualified driver implementation and device-level constraints | Experiment role names or task initialization recipes |
| Driver installation | Implementation identity, supported capabilities, dependency qualification and diagnostics | Physical ownership, scientific parameters or scheduling |
| Setup revision | Logical aliases, roles, channels, wiring/routes, device references and experiment execution policy | Independently editable copies of device connections |
| Resolved execution snapshot | Exact source, setup, parameter, device connection and driver evidence used by a request | Mutable catalog defaults |
| Application resource authority | Admission, leases, connections, claims, maintenance exclusion and recovery | One service per device, source or setup |

Use a stable application-scoped device identity. Display-name changes do not
replace it. A connection revision has its own immutable identity and content hash;
updating its head uses a compare-and-set precondition. Referenced devices are
retired rather than deleting execution evidence. This is a minimal execution
registry, not an asset procurement, repair-history or metrology inventory system.

An editable setup binds logical instrument aliases to stable device identities.
Preparation resolves the selected connection and driver revisions and records
those exact references in the prepared request. A change relevant to those
bindings makes the preparation stale and requires an explicit recheck. Unrelated
devices and display-only changes do not invalidate it.

Admission freezes the resolved combination. A queued or running task does not
follow a later device head. Editing configuration does not disconnect an owner,
retarget a task, or replace a resident driver. An explicit maintenance operation
must drain or cancel affected queued and live work before making an old physical
access path unavailable. If retained execution can no longer be fulfilled, report
that fact; do not silently execute against a replacement device or driver.

Complete resolved configuration snapshots remain useful execution/evidence
carriers. They are not a second editing authority. Keep existing setup identity,
parameter ownership and scientific applicability checks; changing transport or
driver selection does not grant calibration applicability.

Device-wide safety restrictions cannot be weakened by a setup. Experiment start,
success and failure policies remain explicit execution intent, constrained by the
device policy. Import must distinguish these fields rather than moving every
existing `InstrumentSpec` property into the device registry.

## One physical access authority

Names, IP addresses and driver IDs are not sufficient physical identity. Maintain
explicit access aliases and identification evidence for each device. A changed
address can still name the same device; an address reused by a different device
must not silently inherit it. Normalize only identities supported by transport
semantics or a qualified driver. Equal model names are not evidence of identity.
Ambiguous alias association requires user confirmation before physical access.

The resource authority resolves access domains before hardware effects. Catalog
registration cannot grant a second owner merely by inventing another device ID,
logical alias or exclusivity key. Physical identity discovery itself needs a
controlled access reservation; an unregistered connection test is not an escape
hatch. Start conservatively with whole-device ownership. Only qualified capability
contracts may establish independent subdevice access domains.

All of these operations use that authority:

- Experiment execution and interactive sessions.
- Connection tests and explicit identity discovery.
- Temporary diagnostic devices and maintenance actions.
- Idle connection retirement, driver replacement and fault recovery.

Claims and resident connections must resolve through the same identity. Preventing
duplicate submissions while keeping independently keyed live connections is not
sufficient. Changing a connection binding requires quiescence; unknown outcomes
remain fenced until resolved. Reuse the existing leases, actor ownership, task
cancellation and attention mechanisms rather than inventing a management-only
device lifecycle.

## Driver maintenance

Ordinary users see compatible installed drivers, availability, missing dependencies
and useful diagnostics. Maintainers can inspect exact implementation/artifact
identities, validate candidates and update affected devices. Package versions alone
are insufficient evidence of the code executed.

Metadata discovery must not initialize hardware. Separate catalog availability
from loading an optional vendor SDK. A failed driver must not make records or
unrelated devices unavailable. One application can use multiple worker processes;
workers provide execution and fault isolation, not separate user-owned services.

Prepare and qualify an update before switching. Show affected devices and owners;
replace the relevant worker only after quiescence. Preserve task references and
failure diagnostics. An update must not change a live session or silently select a
different driver to satisfy an old request. Retain a usable previous installation
when candidate preparation fails.

VS Code driver development uses an explicitly selected development capability in
an isolated development application home. Refresh code separately from dependency
installation, require affected workers to be idle, and retain the implementation
identity used for execution. This is a capability to implement, not a claim that
editable driver loading is already supported. Arbitrary dependency coexistence,
a public extension marketplace and hot-swapping active devices are not prerequisites.

## Existing implementation and replacement sequence

Delivered #806/#808 behavior is retained: explicit sources, per-request setup
selection, setup-sensitive session retries, shared maintained-key claims and no
global activation during device edits. It does not yet provide a device registry.
Currently `ExecutableSetupSnapshot.instrument_registry` embeds connections and
driver choices; editing a device copies a setup. The runtime obtains its driver
catalog from the backend selected at startup. The raw driver probe calls that
backend without the normal session-claim path. These are replacement boundaries,
not contracts to extend with additional service registrations.

1. Implement device/connection ownership and setup references together with a
   usable device list/editor. Resolve references into existing execution snapshots;
   retire setup-owned connection editing for maintained consumers. A metadata-only
   registry alongside the old authoritative inventory does not complete this step.
2. Establish common physical access resolution and connection ownership for runs,
   sessions, probes and temporary devices. Cover aliases, changed connections,
   admission races, failures and recovery before offering hardware readiness.
3. Move driver discovery, qualification and controlled replacement to application
   capability management. Source registration and device registration never install
   another complete application service.
4. Replace project-service entry and tutorial services with application operations
   and owned simulation-only practice scopes. Keep purposeful worker isolation and
   separate development homes. Remove obsolete selectors and service registry paths
   when their consumers have replacements.

These steps form one pre-hardware capability gate. Do not add compatibility readers
or migration edges for retired development formats. Preserve historical files and
test current-format backup/restore. No supported persistent-data baseline is declared.

## Acceptance journey

Register one simulated device, bind it under different roles in two setups, and
open those setups from two pages and a Python kernel. Registration and inspection
perform no device I/O. One explicit session owns the device; a competing experiment
or connection test waits or rejects before connection. Changing an alias does not
create a second connection. After normal release, another owner can proceed.

Change its connection while a prepared request exists: require a recheck without
modifying recorded evidence. Test a driver candidate with an affected owner: show
the blocker and switch only after the owner ends. A failed candidate leaves the
previous usable installation and unrelated history available. Restart exposes
retained state without replay or silent reattachment.

Run a tutorial with simulated devices in the same application. Its server-granted
capabilities cannot be rebound to registered real devices. Clear its owned tasks,
workers, device instances and records without deleting shared driver installations,
normal device registrations or user-owned exports. Combine this with #616 before
ordinary-user and physical qualification; no Task Manager recovery is acceptable.

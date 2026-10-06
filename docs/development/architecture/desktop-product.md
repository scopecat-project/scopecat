# Desktop product contract and host decision

Status: the bounded product gate was completed in PR #837, including maintainer
Mac/Windows observations. Retain the Python/WebView host (see Technology decision).
PR #844 delivered independent execution and SDK environments; #845 completed their
composition, release and retirement. The sections below retain the acceptance
contract and evidence, not a new queue of unimplemented desktop features.
Data exchange implementation and its current limits are tracked in
[independent data and analysis](data-exchange.md).
The existing GUI, daemon topology and pywebview shell are prototypes. Preserve
scientific invariants and useful components, not their accidental product shape.
Desktop is the primary entry for local files, equipment and external toolchains.
Web rendering is an implementation option; browser access is not a prerequisite.

## Ownership

| Capability | Owner |
| --- | --- |
| Installation, file-open dispatch, windows, menus, shortcuts, whole-app exit | Application |
| Navigation, filters, selection and comparison | Each window |
| Valuable edit recovery | Application data authority; editing remains window-local, with explicit conflict handling. See [draft recovery](draft-recovery.md). |
| Task execution, physical resource exclusion, cancellation and recovery | Execution runtime, independent of windows |
| Record reading, export and external analysis | Data capabilities usable without device runtime, private code or original author environment |

One application may show many windows without creating competing device owners.
Data changes can update all relevant views; navigation in one view must not move
another. Duplicate launch, New Window and Open File are distinct actions.
An HTTP daemon is not a required product boundary. Decide IPC and process layout
after identifying isolation, recovery and remote-access requirements. Do not move
scientific execution into the UI process just to remove the word daemon.

Data-only use means that reading data does not require a device connection,
vendor SDK or author execution environment. It does not mean that the desktop
must bypass its application backend. The same application may start a backend
for data access and task management; starting it must not implicitly initialize
execution capabilities. Open File and experimental work belong in the same
interface, not separate data-viewer and workbench entry modes. Independent Python
readers remain usable without starting the application.

Qualify this as capability independence, not absence of a backend process:
opening a capture may start the ordinary application backend. After a device
activation fails, the same application must still import, read and export data
without retrying that activation. Process separation needs a concrete environment,
fault-isolation or task-lifetime requirement; historical daemon boundaries alone
do not justify separate product modes or duplicate data APIs.

The file-open acceptance condition is: the packaged application's own data
capabilities are sufficient, without a laboratory adapter, vendor SDK, connected
device or user analysis environment. Qualify the dependencies and side effects,
not the presence of a module or process named `server` or `daemon`. The HTTP
capture tests exercise the ordinary backend with device activation forbidden,
including data access after a previous driver activation failed. They are not
tests of a separate viewer runtime.

Installation qualification checks the application package, not a live driver
catalog. Driver availability and failures belong to the device capability and
must not become a prerequisite for opening the application. In particular,
restoring a previously selected driver must not prevent browsing retained data
when its environment is unavailable.

The current device and execution services share one backend owner. Reading
application health or closing an unused application does not activate it;
requesting driver capabilities does. A failed activation leaves data services
available, and a validated driver replacement can supersede the unavailable
selection without loading it. Normal desktop and development application
declarations do not include a bootstrap. Standalone reference projects and test
fixtures can explicitly seed device configurations through bootstrap; those
operations request driver capabilities and are not evidence of data-only startup.
Registering a development source folder does not publish or activate its driver
implementation. Driver activation remains an explicit device-management action.

## Bounded product slice

Use two small synthetic records with different identifiers and known values.
No hardware, SDK, private adapter or editable source is needed.
Use exported captures to qualify portability;
fixtures alone can demonstrate interactions but cannot count as data portability
or data-only runtime evidence.

| Step | Observable acceptance |
| --- | --- |
| Open application, then record A | Understandable empty/open state; record title and source visible; no laboratory chooser or hardware preparation |
| Open record B in a new window | Both records remain visible; each window retains its own navigation and selection |
| Compare and navigate | Back/forward and record selection affect only that view; keyboard focus is predictable |
| Select and copy | Text, IDs and errors can be selected; Cmd/Ctrl-C yields exact text; controls do not globally disable selection |
| Find and zoom | Find within the current view and readable zoom work through discoverable menu commands and platform shortcuts |
| Export | Native save destination, cancellation and failure feedback; exported contents can be opened independently |
| Close one window | Other view remains unchanged; shared work is not stopped |
| Run a synthetic task and close all views | Task continues with an accessible background entry; reopening creates/restores a usable view without replaying the task |
| Quit | One work-aware decision across windows; feedback persists until shutdown; complete native process exit succeeds without a crash report |

Native observations establish platform interaction; automated checks establish
shared data semantics. Revalidate the interactions affected by a change. The
[Windows trial](../windows-application-trial.md) and
[native window acceptance](../native-window-acceptance.md) document bounded probes.

## Technology decision

Decision on 2026-10-03: retain the Python/WebView host for the delivered slice.
Independent windows, native file commands, basic text interaction and lifecycle
behavior have bounded Mac/Windows evidence, and both distributions are
reproducibly qualified. The discovered restoration/history issues were corrected
in shared application coordination; no remaining demonstrated limitation requires
a second shell. Scientific Python and ordinary author environments remain separate
from the packaged application runtime.

This decision accepts the existing Cocoa/WinForms activation, menu, icon and exit
glue as a maintenance cost; it does not declare the GUI finished or promise
unrestricted WebView capability. Reconsider the host only for a concrete required
interaction, accessibility or lifecycle limitation, or recurring platform repair
cost that cannot reasonably be resolved in this boundary. Compare an alternative
against the same user journey then, not at every PR or build. Broad GUI redesign
can proceed independently of this host reuse decision.

## Evidence

[#837](https://github.com/scopecat-project/scopecat/pull/837) records the bounded
Mac/Windows product acceptance and host decision. #844 delivered independent
execution/SDK environments; #845 qualified their composition and release.
The [original observation log](https://github.com/scopecat-project/scopecat/blob/53a74eaae7d2195fa4430eda7d737506d13da9fd/docs/development/architecture/desktop-product.md#current-local-evidence-2026-10-02)
retains dated successes, failures and superseded follow-ups at their tested revisions.

Current native repair evidence is maintained in
[native window acceptance](../native-window-acceptance.md); remaining editor,
unfamiliar-user and physical observations belong to
[#616](https://github.com/scopecat-project/scopecat/issues/616).
The [lifecycle contract](desktop-lifecycle.md) defines close versus Quit.

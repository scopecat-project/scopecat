# Desktop product decision gate

Status: agreed direction and next acceptance scope, not delivered functionality.
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
| Navigation, filters, selection, comparison and unsaved view state | Each window |
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
These boundaries alone do not finish the portable-data desktop journey.

## Bounded product slice

Use two small synthetic records with different identifiers and known values.
No hardware, SDK, private adapter or editable source is needed. Portable-data
reading belongs to milestone PR 2; until implemented, fixtures can demonstrate
interactions but cannot count as data portability or data-only runtime evidence.

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

Verify on Mac and Windows. Record what is observed, what is automated, and what
is fixture-only. A mocked backend or static mockup alone cannot pass this gate.
Include one failed export and one failed shutdown with a usable recovery path.

### Current local evidence (2026-10-02)

An isolated Mac app wrapper loaded the current source from the development
environment and the existing built GUI. The native File menu created a second
window against the same backend. Navigating that window to Data and closing it
left the first window on Experiments. Cmd-Q displayed shutdown progress, stopped
the backend, and released the desktop ownership lock. Automated tests also cover
preserving per-window URL navigation when the backend address changes and keeping
the last native window alive when multiple close requests arrive.

This is source-host interaction evidence, not qualification of a newly packaged
release. It does not cover opening portable records, export, copy/find/zoom,
in-flight experimental work, failed shutdown, or Windows multi-window behavior.
The full bounded product slice remains incomplete.

Local navigation checks now cover page and selected-record history through Back
and Forward. User selections add entries; automatic first-record selection
replaces the current entry so going back cannot create a selection loop. Native
navigation commands target the focused window, and Back stops at its application
entry rather than returning to the host's startup page. This is automated evidence;
native multi-window navigation still needs platform qualification.

### Find and zoom implementation evidence (2026-10-03)

The focused window now receives View-menu Find and Zoom commands. Cmd/Ctrl-F
opens a window-owned find bar; Enter/Shift-Enter, Cmd/Ctrl-G and F3 move through
matches. Escape closes the bar and restores the previous control's focus.
Search uses the WebView text engine with wraparound and explicit no-match feedback.
Cmd/Ctrl-Plus, Minus and 0 share the menu's zoom state (50–200%, reset to 100%).
Ordinary browser access leaves these shortcuts to the browser.

Local frontend checks cover menu events, keyboard routing, browser non-interference,
focus restoration and zoom limits. Native window tests check focused-window
dispatch. Hidden isolated Mac WebViews confirmed `window.find` exists, searches
return true/false for present/absent text, and CSS zoom is supported. Both probes
exited without starting the application backend. These checks do not establish
visible search highlighting, native accelerator behavior, chart clarity under zoom,
or independent zoom across real windows. Those still require the packaged Mac and
Windows journey; they are not grounds for a final shell decision.

## Technology decision

Implement this slice before broad GUI redesign or more host-specific polish.
The current Python/WebView host is a candidate, not a permanent commitment.
Evaluate another shell only against a demonstrated limitation in this slice.
Keep scientific Python unchanged unless a separate requirement justifies change.

Choose using: ordinary interaction quality, independent window state, native
file/menu integration, lifecycle correctness, debugging cost, reproducible
packaging, accessibility and maintenance effort. Record actual platform gaps and
the ongoing glue needed, rather than ranking languages or executable sizes.
If the candidate passes without recurring platform workarounds, reuse it. If a
required interaction or lifecycle remains unreliable, compare one alternative
using the same slice. Ship one selected host and retire the experiment.

## Delivery boundary

PR #829 finishes install/runtime foundations and concrete defects, including text
selection. It must not claim a finished desktop product, multi-window support or
final GUI architecture. Do not expand it into a full GUI rewrite.

Keep the four-PR milestone budget: PR 2 combines independent data/analysis with
this bounded product slice and a recorded shell decision; PR 3 covers execution
and vendor runtime boundaries; PR 4 qualifies their composition and retirement.
If the slice proves a broader UI rewrite necessary, re-plan scope explicitly
before adding PRs or assigning the rewrite to integration closeout.

Next implementation starts with per-window state and application-owned commands,
then two data views and ordinary interactions. Execution integration follows the
same acceptance journey; creating a second copy of the whole console is not the
target. The [lifecycle contract](desktop-lifecycle.md) defines close versus Quit.

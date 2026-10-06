# Desktop product decision gate

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
| Valuable edit recovery (approved target, pending implementation) | Application data authority; editing remains window-local, with explicit conflict handling. See [draft recovery](draft-recovery.md). |
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
These boundaries alone do not finish the portable-data desktop journey.

## Bounded product slice

Use two small synthetic records with different identifiers and known values.
No hardware, SDK, private adapter or editable source is needed. Portable-data
reading belongs to milestone PR 2. Use exported captures to qualify portability;
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

Qualify both platforms using native observations for platform behavior and
automated checks for shared data semantics. Do not repeat the full business
journey manually on both platforms or on every new build. Retain previous
observations unless affected code changes. A mocked backend or static mockup
alone cannot establish native behavior. Failed export and shutdown already have
Mac observations; Windows needs targeted regression for its own differences,
including real open-file deletion behavior, not duplicate manual fault injection.
Use the bounded [Windows check](../windows-application-trial.md) for PR 2.

### Current local evidence (2026-10-02)

Historical checkpoint: this and the following dated subsections record observations
at their respective revisions. Their outstanding checks were subsequently reconciled
in #837/#845; they are not current tasks. Use #616 for remaining observations.

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
or independent zoom across real windows. The later packaged Mac observations and
Windows checklist supply that separate evidence for the host decision.

### Packaged Mac file-open and find check (2026-10-03)

A local package built from `6c0670a2c` opened a synthetic `Alpha.scopecat` through
Cmd-O and the native picker in an isolated application home. The data view showed
run `desktop-alpha`, source `source-project`, four acquisitions and their known
values. Native testing found that `window.find` could match its own query field,
reporting success for absent text. Excluding the find bar fixed that false match,
but returning input focus then removed the visible selection. The corrected
implementation retains the matched range with a CSS Highlight independently of
input focus and restores that range before the next engine search.

A rebuilt local package verified visible highlighting of both `desktop-alpha`
occurrences, forward movement with Enter, backward movement with Shift-Enter,
continued query editing without clicking the field again, and `No matches` for
`definitely-missing-record`. Escape removed the find bar. Cmd-Q displayed shutdown
progress and stopped the isolated backend. These observations qualify this Mac
interaction only; two-window comparisons, full native export, zoom and Windows
qualification are still outstanding. The CSS optimizer warns about the standard
`::highlight` selector but preserves it; the packaged Mac renderer displayed it.

### Packaged Mac save check (2026-10-03)

Native Save cancellation returned to the same data page without claiming success.
An attempted copy exposed a concrete host mismatch: Cocoa SAVE returns a path
string, whereas the bridge assumed a tuple and selected its first character.
That attempted write to `/` failed visibly and left the application usable.
The common save bridge now accepts both host return forms; local regression tests
cover cancellation and exact destinations for capture copies and run exports.

A rebuilt isolated Mac package saved `Alpha-native-copy.scopecat` to the chosen
directory and displayed the complete destination. The result matched the original
archive byte for byte and reopened through `scopecat.open_capture`. The retained
external analysis displayed the `Alpha` fact, its four-row table and figure entry.
Its `Alpha.txt` attachment saved through the native dialog, displayed the saved
path, and contained the exact expected text. This covers capture copy and analysis
attachment saving on Mac; native current-run export, Windows saving and the full
two-window journey remain separate outstanding checks.

### Packaged Mac two-record window check (2026-10-03)

The package containing the Mac save correction used the same isolated home.
Alpha was already imported through the native picker; Beta was added through the
ordinary data API as fixture preparation, not as evidence of native file opening.
The first window selected Alpha and Cmd-Plus twice changed its zoom to 125%.
File / New Window opened a second view on the same backend, initially at 100%.
That view selected Beta and Cmd-Minus changed it to 90%. Its retained analysis
showed the expected `Beta` fact and y values 10, 20, 30, 40; the rendered line
chart, axes and tooltip were readable at that zoom.

Closing the second native window exposed the original Alpha selection still at
125%, on the same backend address. Navigation / Back returned to the unselected
data list; Forward restored Alpha. Cmd-0 reset that window to 100%. This proves
these selection, zoom and close interactions without competing backend owners.
It does not establish all multi-window lifecycle or focused-command behavior.

Both native windows still have the identical title `Scopecat`, and the observed
menu bar has no Window menu listing open views. Cmd-backtick did not switch the
observed window in this trial; this observation alone does not determine whether
the cause is host behavior or automation focus. A discoverable way to identify
and switch data windows remains an unresolved product requirement. Do not call
the comparison journey complete merely because closing one view reveals another.
Clipboard fidelity, work surviving all closed views, failed-shutdown recovery and
Windows qualification also remain outstanding.

### Window identification and switching correction (2026-10-03)

Captured-data views now set the document and native window title to the selected
run ID, source project and application name; leaving the view restores the
ordinary application title. The Mac host installs Cocoa's standard Window menu
on its main loop, with Cocoa maintaining window activation, title changes and
closed-window removal. Existing auxiliary windows are excluded, so the tray's
internal window does not appear as an `Item-0` document. Windows continues to use
the native host title for the operating system's window selection surfaces;
the later four-item Windows checklist includes that behavior.

An isolated rebuilt Mac package displayed distinct Alpha and Beta titles and
listed both in Window. Selecting Alpha from that menu activated its original
data view while Beta remained open. Closing Alpha left Beta usable and removed
Alpha from the menu. A final rebuild verified that the auxiliary `Item-0` entry
was absent. This resolves the observed Mac discovery gap above through native
window management; keyboard cycling, clipboard fidelity, complete work-aware
lifecycle and Windows acceptance are still not established by this check.

### Mac clipboard and invalid-file recovery (2026-10-03)

The isolated `scopecat-window-menu-final` package was checked through native
mouse selection and Cmd-C/Cmd-V, without JavaScript clipboard substitution.
Selecting the Alpha run label and pasting into Find produced exactly
`Run: desktop-alpha`. Opening a deliberately invalid synthetic `.scopecat` file
left the Alpha view and its four acquisitions intact and displayed
`invalid capture: File is not a zip file`; copying that error through the same
system shortcuts reproduced the complete text. Dismissing the error and
cancelling a subsequent native Open dialog preserved the selected Alpha record.
Cmd-Q displayed shutdown feedback and the isolated app and backend exited.

This covers Mac plain-text/identifier/error copying and failed-open recovery.
It does not qualify Windows clipboard behavior, rich-text/table export, work
surviving all hidden views, or recovery from failed shutdown.

### Mac live-run export and close interaction (2026-10-03)

A source-registered, device-free experiment ran one 120-second compute and
returned `42.0` in the isolated packaged application. Closing its sole window
while the run was active did not interrupt execution. Later observation showed
the same run ID, one successful execution segment, one measurement and no second
run. The native Export command saved that completed run through Cocoa's Save
dialog. After Quit stopped the application, an independent Python reader opened
the saved file, verified the original run ID and value, and did not import the
server package.

The automation's window observations refocus the application, so this check does
not establish continuous hidden-window state or a tray-menu restoration action.
Those visual lifecycle steps, failed-shutdown recovery and Windows qualification
remain outstanding. The current-run native export is now observed separately
from the earlier copy-of-imported-file checks.

### Mac external-analysis round trip (2026-10-03)

With the isolated application stopped, a separate Python process opened the
native export from the preceding journey. An ordinary `analysis_function` read
the `answer` measurements and returned a dataclass containing their mean plus an
explicit offset of `8.0`, and their count. It saved a new portable file through
`open_capture(..., output=...)`. Independent reopening verified `mean=50.0`,
`count=1` and the retained implementation fingerprint; the source file's SHA-256
was unchanged. The process imported neither the server nor lab adapter.

The same isolated packaged Mac app then opened the result using Cmd-O and Cocoa's
Open dialog. Its imported-data view displayed the original measurement `42`, the
analysis revision, the raw-measurement input identity, the `summarize` execution
and the saved fact containing `mean: 50` and `count: 1`. Opening the result did not
require running the analysis function again. Cmd-Q displayed shutdown feedback;
process inspection confirmed the application and its backend exited.

The same author script also passed outside the repository in a new Python 3.14
environment containing a freshly built public `scopecat` wheel and its declared
dependencies. Python ran with `-I`, loaded Scopecat from that environment's
`site-packages`, and could not find either the server or lab-adapter module. It
saved and reopened a separate result with the same conclusion, retained execution
fingerprint and unchanged source digest. This qualifies consumer installation
and analysis independently; the native observation above used the first result.

This closes the Mac native-export / external-Python / native-open journey for a
scalar synthetic run. It does not qualify large-waveform analysis or the Windows
desktop journey.

### Mac failure recovery evidence (2026-10-03)

The isolated packaged app was exercised with two reversible external faults;
neither fault required changing its host or backend code. A separate test process
held its application-operation file lock. Cmd-Q displayed shutdown progress,
then the lock timeout appeared in the existing quit dialog and its actions became
available again. After releasing the test lock, Cancel returned to the same
successful run and its measurement `42`. A later Cmd-Q completed normally;
process inspection confirmed both native application and backend had exited.

For the second fault, the synthetic imported capture's object temporarily had no
read permissions. Native Save completed its destination selection, then the view
reported `文件操作失败（HTTP 500）`. The selected run, measurement and analysis
remained visible, and the save action became available again. There was no
destination file or partial-save file. Restoring the original permissions and
retrying saved successfully to the same requested destination, with its full path
shown. An independent public-wheel Python environment reopened that export,
verified measurement `42` and conclusion `mean=50, count=1`, and confirmed the
archive matched the source byte for byte.

This qualifies recoverability of these Mac failure paths, not every shutdown or
storage failure. The raw lock diagnostic and generic HTTP error still need better
wording in the later GUI redesign. It does not qualify Windows failure recovery
or menu-bar restoration after all windows are hidden.

### Packaged Mac large-waveform browsing (2026-10-03)

A new native package built at `fbb14e6a9`, including the waveform-memory fixes,
started against an empty isolated home. Cmd-O imported the independently analyzed
256 MiB capture without an author workspace, private adapter or device setup.
The application displayed opening/checking progress, then 32 acquisitions in their
original descending point order. Each table row showed the 1,048,576-sample shape,
voltage unit and available count as a summary rather than expanding the array.

The waveform view visibly rendered the expected ramp and explicitly reported
4,096 plotted samples out of 1,048,576 source samples. Selecting acquisition 2
changed its source from point 31 / bias 31 V to point 30 / bias 30 V. Switching to
the retained analysis selection changed the table to ascending logical points and
reset the waveform to selected record 1 / point 0 / bias 0 V. The saved analysis
displayed its measurement input identity, execution record and conclusion
`mean=524303, samples=33554432`. No analysis was rerun by opening the file.
Cmd-Q displayed progress; process inspection confirmed native host and backend
exit. This qualifies Mac browsing for this synthetic size and shape, not arbitrary
waveform layouts, multi-gigabyte latency or Windows rendering.

### Packaged runtime and author environment acceptance (2026-10-03)

`scripts/verify_native_application.py` passed against a disposable copy of the
Mac package built from `fbb14e6a9`. The package includes the waveform memory fixes;
subsequent changes at the time of this check were documentation and verifier
repairs. The verifier now uses the shared `DesktopSession` API and resolves macOS
`/tmp` and `/private/tmp` aliases before checking interpreter containment.

The signed application was relocated into a path containing spaces and Chinese
characters. With an empty `PATH`, two native headless launches returned the same
stopped state and preserved application contents. Its packaged runtime created an
independent author environment, registered author sources and completed the
three-point synthetic author journey. The author environment excluded server,
desktop, teaching and JupyterLab packages, and remained usable after the
application copy was moved away. Application signatures and contents were checked
again after the runtime journey.

This qualifies the packaged runtime and environment separation, not native window
interaction. No installer was supplied: DMG distribution, Windows installation
and native platform lifecycle observations are covered by separate checks below.

### File-operation and cleanup closeout (2026-10-03)

A fresh Mac package built from `30a1389b4` passed its headless startup/stop smoke
check, then opened the independently analyzed 256 MiB capture in an empty isolated
home. Cmd-Q during import reported one file operation and one backend change.
Choosing **Quit when work finishes** retained the imported capture and exited;
process inspection confirmed that both the native host and backend had stopped.
Reopening the same home showed the saved imported data.

The ordinary cleanup preview identified that capture and 262,349.2 KiB of owned
files. Confirming cleanup removed its entry, and cleanup history reported records
and files cleared. Reimporting the original file succeeded. During that import,
Cmd-Q again exposed the file-work decision; **Stop and close** displayed stopping
progress and exited without a remaining host or backend. The import completed
before the stop took effect, so this observation qualifies native decision and
exit coordination, not mid-transfer cancellation. Cancellation, preservation of
an existing destination and partial-file removal are covered by automated tests.

Closing the final window left the application running. The maintainer then
confirmed that it stayed hidden and restored normally through **Open Scopecat**
in the menu bar. This is human evidence: the automation's native window reads
raise the window and cannot independently establish continuous hidden state.
After this check, explicit Cmd-Q exited and process inspection confirmed no
remaining test host or backend.

The subsequent multi-window trial exposed an application-level mismatch: tray
hide hid every retained view, but tray open restored only the latest view. Open
now restores every retained view, in creation order, without recreating closed
views or restarting the backend. The same callback serves repeat launch and
macOS reopen. The window-coordination regression covers hide/open of two views
and reopening after one view is closed; all 28 session/platform tests passed.
The earlier single-window observation is not multi-window evidence; the later
Windows checklist acceptance below covers the corrected package.

The first view also retained its startup page in native navigation history:
entering the workbench used a normal URL load. Startup/retry and backend reconnect
now use `location.replace` rather than adding a history entry. This addresses
native back navigation itself instead of relying only on the application's
guarded Back command. Session regressions cover initial entry, retry and both
windows reconnecting while retaining their record routes. The corrected package
was included in the later Windows checklist acceptance below.

The following native-distribution run failed on both platforms in the acceptance
script, after packaging: its fake window still implemented `load_url` rather than
`run_js`. The verifier now records and checks replacement navigation. Desktop
Ctrl-Q and a File/Quit entry also request the ordinary application-owned Quit
decision; startup/recovery pages support Ctrl-Q too. Alt-F4 retains window-close
semantics. Local checks passed 37 Python session/file tests and five UI Quit tests,
including file-work waiting via Ctrl-Q and prevention of duplicate pending quits.
Short transfers do not require repeated human attempts to catch the operation;
deterministic cancellation and file-integrity tests remain the evidence for that
case. The maintainer subsequently completed all four Windows checklist items
with the `13f365110` package and reported no obvious problems. Both native
distribution jobs passed; the subsequent test-only correction at `9ecbd5142`
passed ordinary CI and all 390 frontend tests. This closes the bounded native
acceptance, not exhaustive platform or arbitrary-data-size qualification.

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

## Delivery boundary

The four-part desktop milestone below is completed (#829/#837/#844/#845).
Earlier dated observations describe limits at their recorded revisions; later
accepted evidence supersedes their outstanding-check lists. Current remaining
qualification belongs to #616, not a new four-PR budget.

PR #829 finishes install/runtime foundations and concrete defects, including text
selection. It must not claim a finished desktop product, multi-window support or
final GUI architecture. Do not expand it into a full GUI rewrite.

#837 combined independent data/analysis with this bounded product slice and a
recorded shell decision; #844 delivered execution/vendor runtime boundaries;
#845 qualified composition and retirement. A broader GUI redesign is a separate
future scope, not unfinished integration closeout.

Per-window state, application-owned commands and two data views are implemented.
The bounded platform observations and host decision are complete. The
[lifecycle contract](desktop-lifecycle.md) defines close versus Quit.

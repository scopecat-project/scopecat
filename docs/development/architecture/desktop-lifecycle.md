# Desktop application lifecycle

This describes lifecycle behavior. See the
[desktop product contract](desktop-product.md) for the host decision and
[native window acceptance](../native-window-acceptance.md) for tested revisions and limits.
The installed application owns windows, menus and whole-application exit.
Windows own independent view state, not device or task lifetimes. Execution has
one resource authority across windows; that does not require one permanent HTTP
daemon or starting experiment execution to read data.

| User action or event | Required result |
| --- | --- |
| Open Scopecat | Show the window before preparing environments or starting the service. Enter the workbench when ready. |
| Open it again | Activate an existing window, or create a view if all windows are closed. Never create a competing device owner. Explicit New Window opens another view. |
| Preparation fails | Keep the window usable, explain the failed step and offer retry and exit. Do not silently open another version. |
| Close a window | Close that view without cancelling tasks or changing other windows. Last-window behavior is specified below. |
| Continue in background | Hide the window, retain its host and a visible menu-bar/tray entry with Open and Quit. No invisible service-only state. |
| Quit from the system entry | Apply one application-wide work decision: return, wait for completion, or cancel work and quit. Close all windows only after successful shutdown. |
| Shutdown fails | Keep the host and recovery controls available; never describe a live service as stopped. |
| Replace the installed app | Next launch prepares and activates that package's version when safe. Active work requires an explicit decision; no silent old-version fallback. |

The host's own startup and recovery page must not depend on HTTP service readiness,
author imports or driver qualification. Preparation runs outside the UI event loop.
Application update qualification checks its own runtime and capabilities. Registered
author folders are independently resolved when used; a moved folder or broken author
environment must not prevent opening retained data or other sources after an update.
Only a completed initialization receipt suppresses first-use setup on retry.
Window hiding is not termination. JavaScript bridge replies must finish before
destroying their window. All normal quit paths release the service before removing
the visible application entry.

Native file dialogs and transfers serialize within their own window, not under
the lifecycle lock for their full duration. Active transfers participate in the
shared Quit decision: waiting defers shutdown until completion, while Stop and
close requests cancellation and waits for file workers to settle. Partial saves
never replace the destination. Import cancellation is not rollback of a capture
already committed by the backend. Environment replacement is unavailable during
transfers. A failed or unresponsive transfer leaves a usable exit recovery path.

The last-window policy is the same on Mac and Windows: closing the last window
hides it and retains the application with a visible menu-bar/tray entry, whether
idle or busy. Reopening restores the retained view. Closing an additional window
closes only that view. Closing a window does not request application exit;
explicit Quit is always work-aware. Do not introduce idle automatic exit or
different close semantics by platform. A data-only window must
not initialize hardware. Window-local selections and navigation must not become
application-global state merely because windows share data.

An installed package includes its host dependencies. It must not bootstrap a host
from a mutable selection in the data directory. Retained execution environments
support service/author execution and source evidence; they do not choose which
desktop application opens. Developer entry remains explicit, isolated and does not
open a browser. Do not add migration paths for retired development installations.

Qualification must cover fresh installation, package replacement, active work,
background/reopen, startup failure, failed shutdown and recovery after process
termination. Native UI checks on macOS and Windows are separate evidence from
headless process tests. Hardware acceptance follows software lifecycle acceptance.

The [native window probe](../native-window-acceptance.md) runs in the manual
distribution profile. [Editing recovery and store isolation](draft-recovery.md)
share this lifecycle without changing close or Quit behavior.

“Quit when work finishes” starts a reversible drain. Under the same admission
lock as shutdown, ordinary mutations and answers stop being admitted. Already
admitted procedure effects and executor cleanup keep their existing lease and
identity checks. Necessary publications outside a procedure URL carry the exact
live step identity; retained analysis workers carry an identity for their in-flight
call and run. Neither permits new tasks, answers, retries or fault resolution.
Desktop file operations also stop admitting new work.

New procedure submissions and finite calibration tasks default to `current` work.
Authors may declare `subsequent` or `maintenance` through the submission/task
contract. The declaration is retained and cannot change on an idempotent retry.
Records without a declaration remain unknown and block automatic exit; exact
retries return those same unknown records. Current finite calibration plans can
finish their bounded stages. Other plans stop starting another stage while their
admitted children settle. Cancelling the exit wait restores admission, without
resetting cancellation, pause or faults. Closing the dialog acknowledges
cancellation even if the original wait response was lost.

A human-input checkpoint permits exit only with the exact durable question,
inputs, source and retained results, no procedure lease or unsettled latest effect,
the matching input handoff, and a successfully exited managed worker. The manager
records successful process settlement against its PID/create-time receipt. A dead
PID without that observation, a live orphan process, user pause, failed worker,
resource wait, unknown scope, active child, device session or quarantined resource
remains blocking. Waiting input stays waiting, never completed. Disconnect is not
proof that physical outputs have stopped. Exit does not answer, retry, remeasure,
resolve attention or clear isolation.

Pending schedules remain persisted without materialization during drain. The
existing due-time and bounded scan policy is unchanged on reopening; no
catch-up-all pass or silent cancellation is added. Analysis follows stop admitting
the next round. Whole admitted rounds, including final persistence, remain active;
cancelling drain resumes the same runner. Unresolved follow attention blocks exit.

The dialog refreshes work finishing now, retained deferred work and identified
blockers. Failed shutdown leaves recovery controls available. Automatic shutdown
repeats the safety report under both HTTP admission and background-round fences;
the display is not authority to shut down. Tests cover fake-device SDK execution
and analysis during drain, real cloud worker settlement and reopening, retained
process publication, admission races, lost replies and browser cancellation.
They do not establish native window behavior on a user's Mac/Windows installation
or physical hardware safety.

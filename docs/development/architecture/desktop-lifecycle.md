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

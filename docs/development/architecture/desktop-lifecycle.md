# Desktop application lifecycle

This is the target for the desktop convergence batch, not an acceptance report.
The installed package owns the desktop host and expected application version.
The host owns the window, visible background entry and experiment-service lifetime.
The experiment service remains the single authority for tasks, data and devices.

| User action or event | Required result |
| --- | --- |
| Open Scopecat | Show the window before preparing environments or starting the service. Enter the workbench when ready. |
| Open it again | Activate the existing window for this data home; never start a second owner. |
| Preparation fails | Keep the window usable, explain the failed step and offer retry and exit. Do not silently open another version. |
| Close with no active work | Stop the owned service and exit. An idle Python connection is not active work. |
| Close with active work | Explain experiments, manual device sessions and maintenance affected. Offer return, wait for completion, or cancel work and quit. |
| Continue in background | Hide the window, retain its host and a visible menu-bar/tray entry with Open and Quit. No invisible service-only state. |
| Quit from the system entry | Follow the same work-aware exit path as the window. |
| Shutdown fails | Keep the host and recovery controls available; never describe a live service as stopped. |
| Replace the installed app | Next launch prepares and activates that package's version when safe. Active work requires an explicit decision; no silent old-version fallback. |

The host's own startup and recovery page must not depend on HTTP service readiness,
author imports or driver qualification. Preparation runs outside the UI event loop.
Only a completed initialization receipt suppresses first-use setup on retry.
Window hiding is not termination. JavaScript bridge replies must finish before
destroying their window. All normal quit paths release the service before removing
the visible application entry.

An installed package includes its host dependencies. It must not bootstrap a host
from a mutable selection in the data directory. Retained execution environments
support service/author execution and source evidence; they do not choose which
desktop application opens. Developer entry remains explicit, isolated and does not
open a browser. Do not add migration paths for retired development installations.

Qualification must cover fresh installation, package replacement, active work,
background/reopen, startup failure, failed shutdown and recovery after process
termination. Native UI checks on macOS and Windows are separate evidence from
headless process tests. Hardware acceptance follows software lifecycle acceptance.

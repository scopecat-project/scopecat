# Recoverable editing and native storage

The product principle approved on 2026-10-06 is: **valuable edits are recoverable
by default**. Application-owned recovery is implemented for
[Decision drafts](decision-drafts.md) and [parameter working tables](parameter-drafts.md).
Other draft types still need their own lifecycle rather than a generic draft
framework. The native store contract and its acceptance remain separate below.

## Editing contract

Navigation, opening another window and normal Quit must not discard valuable
input. Restart must restore it. Automatic draft persistence is separate from
saving a parameter version, applying or publishing a change, and submitting an
experiment. Recovery never authorizes those actions or starts acquisition.
Intermediate or invalid input is still valuable and must survive without passing
scientific validation. Credentials, tokens and other secrets are not automatically
included in this draft contract.

A draft retains its logical target, original baseline and revision. If the
baseline changes, preserve the input and require revalidation before explicit
submission; do not lose it merely because an intent hash changed. Concurrent
edits must retain conflicting versions for a user decision, not silently overwrite
one another. Explicit discard is scoped to the intended draft. Each type needs
an explicit retention and completion policy before implementation; unrelated
navigation is never its deletion policy.

Selection, filters and navigation remain window-local. Recovering edits does not
require restoring the last viewed run, changing the default Runs entry, linking
windows, or introducing a multiple-workspace manager. The existing window-close,
background and work-aware Quit semantics remain unchanged.

## Experiment-form boundary

The experiment launch form currently keeps editable inputs and its original
submission attempt in window memory. Navigation within that window can retain
input; reload/restart does not restore it. Changing control declarations can
replace control edits with new defaults. A server-admitted procedure remains
retained independently of the window's original-request panel. These boundaries
do not satisfy the editing contract above and are distinct from parameter and
Decision draft persistence.

Experiment recovery needs its own logical target, baseline/revalidation, conflict,
retention and completion policy before implementation. Recovery of an uncertain
submission must retain the original exact request identity before sending,
independently of editable input, and cannot authorize acquisition. Browser storage
or matching mutable inputs is not a substitute for that identity. See
[the ordinary-entry PR evidence](https://github.com/scopecat-project/scopecat/pull/930)
for the observed cases; broader lifecycle choices remain separate work.

## Application drafts and native storage

Application drafts use typed read/save/discard operations with conditional writes
under the existing data authority. Their type-specific retention and completion
policies are documented in [Decision drafts](decision-drafts.md) and
[parameter drafts](parameter-drafts.md). Current-format recovery includes this
data without introducing prebaseline migrations.

Native storage separately isolates actual hosts and preserves the intended store
across windows. Application draft recovery cannot hide browser cookie or marker
destruction; the native probe checks both independently.

The service binds a loopback port dynamically. localStorage belongs to the
scheme/host/port origin, not an application home or URL path. Persisting a browser
profile alone cannot restore drafts after a port change; port reuse can also
expose another home's storage if the profile is shared. Keep authoritative drafts
in the application owner. Do not reserve a fixed port or change routing, CORS,
authentication or permissions as an incidental workaround.

## Approved native store repair

On 2026-10-06 the user approved a bounded, locked production Cocoa dependency
patch: windows in one actual host share an in-memory website store; host exit
ends that store's lifetime. Valuable drafts recover through application data.
There is no requirement to retain browser cookies across restarts.

The unmodified pywebview 6.2.1 Cocoa backend creates a WKWebView before assigning
its store, then clears the default website store for every private window.
The [pinned backend](https://github.com/r0x0r/pywebview/blob/2fc63cd6a92b8630c14f6d4dd73f9691591f8eea/webview/platforms/cocoa.py)
and upstream commit `e0c008ced7313f5d4b18ffc2bdd63e010755f63d` have this behavior.
The Darwin delivery now transforms that exact wheel into
`6.2.1+scopecat.1` using `lab_tools.cocoa_dependency` and the included
`patches/pywebview-6.2.1-cocoa.patch`. Source wheel, source module, patch, resulting
module and resulting wheel each have fixed SHA-256 checks. The transformed wheel
retains the upstream license, rewrites METADATA/RECORD, and carries its provenance
in `scopecat-cocoa-patch.json`. Final requirements and bundle inventory hash the
transformed artifact. The repository dependency stays pinned to upstream 6.2.1;
**ordinary source development does not receive this repair**. Windows delivery at that checkpoint
retained the upstream wheel; its later approved repair is described below. The packaged repair passed the bounded native checks
on combined candidate `4aee5825dbe374f469bc0f1be1e9a981908086c1` in
[run 37436328157](https://github.com/scopecat-project/scopecat/actions/runs/37436328157).

The patch holds one `nonPersistentDataStore` strongly in the Cocoa host class,
sets it on the configuration before constructing each private WKWebView, and
sets `self.datastore` from the constructed WebView's actual configuration. It
never imports or clears default/NSHTTP stores. Nonprivate mode retains its
existing behavior; production continues to use private mode. Cookie interfaces
still use `self.datastore`. The patch also corrects the upstream getter spelling
`SameSitePolicy()` to Apple's public `sameSitePolicy()` so real cookie reads can
complete. Existing `clear_cookies` semantics (all website data) are unchanged and
have no product caller.

Apple's [public API](https://github.com/WebKit/WebKit/blob/6ed1f44d538f5e4c44098bd910c7d2e0d38ec00c/Source/WebKit/UIProcess/API/Cocoa/WKWebsiteDataStore.h)
provides the nonpersistent store from macOS 10.11. The patch introduces no macOS
14 requirement and does not qualify old OS releases or the complete dependency
stack on those releases. Construction order matters because the
[WebView initializer copies configuration](https://github.com/WebKit/WebKit/blob/6ed1f44d538f5e4c44098bd910c7d2e0d38ec00c/Source/WebKit/UIProcess/API/Cocoa/WKWebView.h).
Historical [cookie isolation problems](https://github.com/r0x0r/pywebview/issues/531#issuecomment-1064838393)
require current native testing; they do not establish that this API is unusable.

Persistent UUID stores, machine tokens and profile directories are not part of
this repair. Simply disabling private mode would retain shared/default browser
storage and is not the approved solution. No global storage clearing, test-only
backend replacement, system security setting or permission change is introduced.

### Windows cookie initialization repair

On 2026-10-06, native run `37451728337` at `0dc08bd3` demonstrated that opening
another Windows WebView deletes the original window's cookie. Marker and
application draft survived; cross-host isolation and complete restart/form
recovery passed. The user then approved a bounded repair to initialize cookies
once per actual host/profile, preserving private mode and explicit clearing.

Windows delivery transforms the hash-locked upstream wheel into
`6.2.1+scopecat.windows.1`. The reviewed patch changes only `edgechromium.py`;
WinForms and its host-specific temporary directory are unchanged. It uses
[ClearBrowsingDataAsync(Cookies)](https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2profile.clearbrowsingdataasync?view=webview2-dotnet-1.0.3856.49)
to observe completion rather than treating the void `DeleteAllCookies` call as a
completion barrier. The pinned wheel's WebView2 Core DLL supports this API.

A host-local map keyed by the actual environment folder and profile name retains
one task, including failure. A lock covers first-task creation, never waiting for
completion. Each view continues on its own STA UI scheduler after success;
no UI message pump is blocked and no foreign view's COM object is shared.
URL/HTML requests before completion queue only their last navigation. Faults,
cancellation and synchronous initialization errors never navigate or retry a
later clear. Closing a waiting view does not cause navigation into a disposed
WebView. Nonprivate navigation, explicit `clear_cookies`, and host cleanup retain
the upstream behavior. The change does not access default/global user profiles,
introduce persistent browser storage or alter application draft ownership.

Source module, patch, resulting module, wheel and provenance are hash-checked;
METADATA/RECORD are rewritten deterministically and licenses retained. Only the
existing RECORD/ZIP writer is shared with Cocoa; Darwin version, patch and final
wheel hash remain unchanged. Ordinary source environments still use upstream.
The strict existing native cookie/marker/restart assertions must pass on the
repaired exact SHA before claiming Windows qualification. Source-level sequencing
tests and real wheel-integrity checks are not native acceptance.

### Home, host and privacy boundaries

The existing desktop lock gives one actual host per resolved home; duplicate
launch activates it. All its windows share one private store. Distinct hosts get
separate store instances even with identical package identity and URL origin.
Closing a secondary view does not discard the host's store or stop its runtime.
Last-window background and work-aware Quit semantics remain unchanged.

The native package keeps `org.scopecat.desktop`. Neither copying the package nor
changing a port/home directory is an isolation proof. A copied/restored data home
carries recoverable application drafts, not a browser profile identity. Browser
state expires with its host; uninstall still must not delete application drafts
or scientific data. OS framework behavior and cookie isolation need the native
checks below; an isolated application directory alone is insufficient proof.

## Native acceptance

[Native window acceptance](../native-window-acceptance.md) owns the exact tested
candidates, failed observations, pending checks and probe commands. Its independent
assertions cover same-host windows, overlapping hosts at the same origin, default
store isolation, full host/service restart and application-owned draft recovery.
The probe checks the actual configured store and packaged dependency identity;
source transformation and wheel-integrity tests cannot establish native behavior.

Decision draft persistence is delivered in
[#886](https://github.com/scopecat-project/scopecat/pull/886). Earlier combined-branch
qualification records are retained in the acceptance page; they do not imply every
later merge combination was natively executed. Native probes run in the manual
distribution profile, separately from fast PR CI. Remaining human/platform and
physical evidence belongs to [#616](https://github.com/scopecat-project/scopecat/issues/616).

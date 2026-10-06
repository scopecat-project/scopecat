# Recoverable editing and native storage

The product principle approved on 2026-10-06 is: **valuable edits are recoverable
by default**. This is an approved target, not a delivered persistence feature.
The first implementation should be bounded to the existing Decisions draft;
other draft types need their own lifecycle rather than a generic draft framework.

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

## Two independent repair contracts

1. **Application-owned recovery:** add bounded typed read/save/discard operations
   for Decision drafts under the existing application data authority. Retain
   logical target, baseline and a revision for conditional writes; expose conflicts
   without last-writer-wins loss. Persist current-format valuable drafts as data,
   not cache or author-environment files. Include them in current-format recovery
   coverage without introducing prebaseline migrations. Decide the concrete schema
   and completed/discarded draft retention in that implementation review.
2. **Native store isolation:** stop window creation from clearing the shared
   default website store. Every window in one home must use its intended store;
   another home or host identity must not share it accidentally. Never clear a
   global/default store to simulate privacy. This repair must pass the independent
   marker assertion as well as draft recovery; a new backend draft copy must not
   hide native data destruction.

The service binds a loopback port dynamically. localStorage belongs to the
scheme/host/port origin, not an application home or URL path. Persisting a browser
profile alone cannot restore drafts after a port change; port reuse can also
expose another home's storage if the profile is shared. Keep authoritative drafts
in the application owner. Do not reserve a fixed port or change routing, CORS,
authentication or permissions as an incidental workaround.

## Supported store choices and remaining decisions

The pinned pywebview 6.2.1 Cocoa backend creates a WKWebView and then, for each
private window, asynchronously clears all data in the default website store.
Its Cocoa backend does not implement home isolation through `storage_path`.
The same constructor clearing block was present in upstream commit
`e0c008ced7313f5d4b18ffc2bdd63e010755f63d`; an upgrade is not an established fix.
See the [pinned backend](https://github.com/r0x0r/pywebview/blob/2fc63cd6a92b8630c14f6d4dd73f9691591f8eea/webview/platforms/cocoa.py)
and [upstream constructor](https://github.com/r0x0r/pywebview/blob/e0c008ced7313f5d4b18ffc2bdd63e010755f63d/webview/platforms/cocoa.py).

| Choice | Isolation and persistence | Decision |
| --- | --- | --- |
| Current production defaults | Mac clears the shared default store per window; current Windows probe passes within one process | Retain the failing probe; not a recovery solution |
| Set `private_mode=False` alone | Avoids the Mac clearing branch but leaves a default store; Windows falls back to a shared pywebview profile path | Reject as an unscoped fix |
| Explicit persistent store per home | Windows supports `storage_path` with nonprivate mode. macOS 14+ supports a persistent WKWebsiteDataStore identified by UUID | Recommended native design candidate, subject to isolation/privacy approval and backend support |
| Shared nonpersistent store within one host | Can separate browser lifetime from durable application drafts | Possible alternative, but still requires Cocoa backend support and cookie/privacy qualification; not an automatic fallback |

Apple's [public website-store API](https://github.com/WebKit/WebKit/blob/main/Source/WebKit/UIProcess/API/Cocoa/WKWebsiteDataStore.h)
provides UUID-based persistent stores and targeted removal on macOS 14+, not an
arbitrary application directory. The store must be configured before constructing
the WebView because its [configuration is copied](https://github.com/WebKit/WebKit/blob/main/Source/WebKit/UIProcess/API/Cocoa/WKWebView.h).
pywebview currently exposes no supported Cocoa parameter for this selection.
An upstream API/backend change or separately reviewed dependency change is needed;
a test monkeypatch, private global-state switch or late window event is not a fix.
Confirm the supported minimum macOS version before choosing this API. Historical
[pywebview cookie isolation problems](https://github.com/r0x0r/pywebview/issues/531#issuecomment-1064838393)
also require native cookie checks; a store identifier alone is not complete proof.

On Windows, an explicit writable profile directory under the resolved data home's
`desktop` directory is the candidate. WebView2's [user-data-folder contract](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder)
includes cookies, permissions and caches, not just drafts. Enabling persistence
therefore changes privacy behavior and needs separate approval. No such option or
dependency is changed by this document.

### Home, host and privacy boundaries

The existing desktop lock gives one host per resolved home; another launch of
that home activates it. Distinct homes have distinct host locks, which currently
do not isolate the Cocoa default store. Additional windows retain one runtime and
resource owner. No cross-home physical-device ownership guarantee is added.

A proposed persistent-store binding must be host-generated, machine-local and
scoped to the resolved home. All its windows use the same binding; different
homes must not. On copy/restore or a changed home binding, create a fresh browser
profile rather than importing a copied UUID. Durable application drafts still
recover from application data. Exact binding metadata and move behavior require
implementation review; they are not delivered features. A Mac UUID store is
OS-managed outside the home, so filesystem containment must not be claimed.

Browser profiles are not scientific backup payloads. Software uninstall preserves
application data; do not silently delete valuable drafts or historical files.
Define explicit profile removal separately from draft discard and scientific-data
deletion. Removal may target only a verified owned directory or UUID, with its
host stopped; never enumerate and clear shared/default data. Persistence of
cookies, credentials or permissions, OS-managed profile retention after uninstall,
and supported-platform behavior must be resolved before enabling a store policy.

## Minimal implementation and acceptance order

First implement the bounded Decision draft operations and UI recovery against the
existing data owner, coordinating App/Launch semantics with their owner. Test
same-home restart with a changed port, invalid input, changed baseline and two
conflicting editors. Scope workspace changes to restoring that workspace's input
while invalidating preview and execution authority. Do not build a global draft
framework or alter publication/submission semantics.

Separately prepare the explicit store binding and required Cocoa backend support.
Before implementation, obtain approval for browser persistence/privacy policy,
minimum OS requirements and any upstream dependency change. No security setting,
user Mac data, repository ruleset or release is authorized by the product principle.

Local tests can verify binding selection and draft ownership; they cannot qualify
native storage. On a final reviewed candidate, use the normal user-triggered
manual native-distribution workflow on disposable hosted Mac and Windows VMs.
Retain the current exact-run/window/marker checks. Add evidence that the selected
store is correct, an unrelated default-store sentinel survives, distinct homes do
not leak, duplicate same-home launch has one owner, and a copied home does not
reuse its browser identity. Verify actual browser persistence across host restart
at a deliberately stable test origin, separately from draft recovery across changed
service ports. A random port must not masquerade as either privacy or persistence.

No additional native dispatch is required for this documentation change. Mac
storage remains failed at the [recorded candidate](../native-window-acceptance.md).
OS focus, actual menus, tray and visual presentation remain separate manual
boundaries; neither unit tests nor platform-smoke close them.

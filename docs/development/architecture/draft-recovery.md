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
**ordinary source development does not receive this repair**. Windows delivery
retains the upstream wheel. This implementation is not yet native-qualified.

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

## Bounded acceptance and independent draft work

Decision draft persistence is implemented independently in draft
[PR #886](https://github.com/scopecat-project/scopecat/pull/886). It is not included
in this native branch. Combined recovery evidence must name an actual combined
candidate; neither branch's checks establish qualification of the other.

Local checks verify source transformation, wheel integrity and probe contracts;
they cannot qualify Cocoa. The manual native-distribution profile retains the
real exact-run/window/independent-marker checks and adds a bounded Cocoa check:

- A seeds and reads back default WK and NSHTTP cookie sentinels before private
  windows exist. Two native windows share the actual nonpersistent store, write
  and read each other's markers, and expose the browser cookie through the
  existing cookie API.
- B overlaps A at the same fixed test origin and original package identity. B
  starts empty; its writes and clear operation do not change A. Default/NSHTTP
  sentinels are neither imported into document cookies, cookie API or actual HTTP
  requests, nor removed by private-window creation or clearing.
- After A exits, C starts at that same origin and must not recover A's marker or
  cookie. This tests browser ephemerality, separately from application-owned draft
  recovery across service restarts and changed ports.

The probe checks the actual configured store and exact packaged dependency hash;
it does not replace the backend. All native checks remain hosted-only and outside
the fast PR matrix. Use one user-triggered manual run for the final reviewed
candidate. The [previous Mac failure](../native-window-acceptance.md) remains the
latest native evidence until then. OS focus, actual menus, tray and visual
presentation remain separate manual boundaries.

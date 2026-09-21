# Platform status and remaining work

This is the current integration map, including launcher convergence (#659),
Help/navigation (#658), stopped environment rechecks (#663) and retryable fixed
delivery publication (#665), rather than another historical roadmap. The umbrella is [#610](https://github.com/scopecat-project/scopecat/issues/610).
Earlier wave reports describe their own revisions; do not interpret their pending
items as additional work when a later slice delivered them. Update this map when
closing an ownership or product workflow, and keep implementation details in the
linked architecture documents.

## Delivered foundations

| Boundary | Delivered | Remaining limit |
|---|---|---|
| Scientific selection | Catalog-qualified single-member targets in notebooks and the workbench; exact preview, plan and parent/child evidence (#642, #655) | Multiple members and target connections cannot execute |
| Parameters and calibration | Independent working-point heads; verified publication and bounded automatic cohorts use exact owner/sample/batch/config evidence (#648, #652) | No executable apparatus subject or qualified cross-object dependency |
| Executable setup | Immutable revisions, independent activation, explicit parameter rebinding and resource-generation fencing (#654) | One active setup per deployment; whole-setup content fence, not minimal experiment dependencies |
| Author sources | Same-environment registered workspaces, scoped publication/workers and page-local code selection (#636, #657) | Different environments and portable multi-source installation are not qualified |
| Application entry | Registered workbenches and lifecycle operations (#627, #633); workbench Help and manager-preserving navigation (#658); stopped environment revalidation (#663); retryable fixed delivery publication (#665) | Host still supervises separate services; not shared cross-service physical authority |
| Author/results | Direct callable compute, inferred scalar outputs, dict returns and validated native historical result rows (#584, #586) | Symbolic argument static typing remains broader than native calls |
| Grouped analysis | Request sweeps and retained completed-run group analysis | No durable live-group completion/scheduling protocol |
| Data recovery | Current-format backup/restore and non-mutating rejection of unsupported formats | No supported persistent baseline or selected-run exchange format |

See [configuration ownership](architecture/configuration-ownership.md),
[workspace bindings](architecture/workspace-bindings.md),
[target execution](architecture/target-execution.md) and
[application host](architecture/application-host.md) for the actual contracts.

## Near-term sequence

1. **Make public the ordinary experiment application** under
   [#671](https://github.com/scopecat-project/scopecat/issues/671).
   The Windows trial confirmed the installation path works but the product still
   feels like a teaching manager. Follow the
   [public application contract](architecture/public-application.md): standard
   laboratory capability declarations (#672), direct primary-workbench entry
   (#673). First-run source-directory creation/connection now opens an ordinary
   workbench. Explicit local settings selection and stopped-service rechecks (#681)
   replace ambient bootstrap profiles without changing existing scientific config.
   Installed adapter manifests and independently editable experiment folders (#683)
   now use recorded package content identity. A selected fixed offline delivery can prepare a new ordinary project environment
   (#685). Already registered author code can open its existing laboratory with
   `scopecat app --workspace PATH`, using the registered environment and selecting
   page-local code without creating another service (#706). SDK ownership, Python provisioning, environment updates and code/parameter
   trial workflows remain next. Native installation and shared device authority remain later
   explicit contracts. The bounded installed/browser checks in #668 passed on
   Windows/Linux; broader qualification remains tracked in #616. Do not repeat
   tutorial acceptance as a substitute for the real laboratory connection journey.
2. **Remove demonstrated consumer and fixture debt** under
   [#615](https://github.com/scopecat-project/scopecat/issues/615).
   Frequency/amplitude and temperature already use authored discovery; channel
   timing retains its multi-stage workflow. Each worker request resolves its
   composed catalog once. Transport qualifies source ownership before dispatch;
   pinned requests use revision workers, and the one-shot worker serves
   baseline-less maintained applications. Further work is targeted fixture
   extraction (#565, #520) and an inventory of actual remaining consumers.
   Record concrete replacement coverage before removing any reference fixture.
3. **Add live grouped analysis** under
   [#561](https://github.com/scopecat-project/scopecat/issues/561).
   Start with durable group-completion identity, generation/repetition semantics and
   reconnect cursors, then bounded asynchronous analysis and incremental plots.
   Reuse the completed-run analysis API rather than adding a teaching-only path.
4. **Extend scientific subjects with a concrete workflow** under
   [#612](https://github.com/scopecat-project/scopecat/issues/612).
   A no-sample line measurement and explicit chip-to-line calibration dependency
   require an execution/applicability contract. Descriptive apparatus notes and
   room-temperature attachments are not automatic low-temperature calibration.

Same-environment source ownership is delivered;
[#613](https://github.com/scopecat-project/scopecat/issues/613) retains environment
and installation boundaries, with installed qualification tracked in #616.
#614 is closed for its delivered entry/lifecycle scope. Installation pairing and a
simpler replacement-environment workflow need a separately bounded design; they
are not implicit unfinished requirements of that closed issue. The current
[maintenance guide](../how-to/maintain-application.md) documents explicit local
registration when paths change. Changing the manager delivery does not redirect registered runtimes. An explicit
stopped laboratory update now prepares a separate delivery environment, qualifies
registered sources and switches their interpreter bindings while retaining IDs and
data paths (#713). The combined public/private checkout/build/setup workflow remains
#712; the registered Notebook entry below handles JupyterLab interpreter selection.

## Debt and evidence boundaries

- **Consumer duplication:** maintain a call-path inventory, distinguish advanced
  maintained workflows from generic authoring, and replace both writers and readers
  together. The current `legacy` service workspace is a valid identity, not an
  old-format migration fallback to delete mechanically.
- **Fixture cost:** [#565](https://github.com/scopecat-project/scopecat/issues/565)
  and [#520](https://github.com/scopecat-project/scopecat/issues/520) retain targeted
  extraction work. Reference lab onboarding and unnecessary autouse daemon setup
  are already retired. Preserve real routing, compiler, resource and recovery
  evidence; moving files alone is not decomposition.
- **Windows reliability:** [#465](https://github.com/scopecat-project/scopecat/issues/465)
  and [#553](https://github.com/scopecat-project/scopecat/issues/553) have diagnostics,
  not demonstrated root-cause fixes. Reproduce/narrow against the new milestone;
  successful reruns, extra retries or longer deadlines do not close them.
- **Performance:** [#523](https://github.com/scopecat-project/scopecat/issues/523)
  needs matched current-host cold/warm and long-session evidence. Historical timing
  reports are not current budgets. Recent fast PR gates do not establish installed
  Windows reliability or hardware performance.
- **Author typing:** [#581](https://github.com/scopecat-project/scopecat/issues/581)
  must reconcile remaining symbolic argument typing with delivered compute and
  native-reader capabilities; do not reimplement those completed slices.

## Deliberately later

The first supported data baseline and bounded upgrades are
[#574](https://github.com/scopecat-project/scopecat/issues/574); self-contained
selected-run exchange is [#575](https://github.com/scopecat-project/scopecat/issues/575).
Neither requires resurrecting development migration chains. Keep retained files
untouched under the [data policy](data-compatibility.md).

Tray/login startup, LAN authorization, heterogeneous environments, concurrent
incompatible setups and the graphical editor direction
[#502](https://github.com/scopecat-project/scopecat/issues/502) need their own bounded
contracts. Do not expand these into an implicit requirement for the next local
milestone. Teaching remains isolated until shared practice/real execution has
server-enforced resource and publication boundaries.

Fast public CI, self-review and squash merging remain the integration gate.
Milestone acceptance remains explicit; private Actions stay disabled.

### Author catalog declaration

`authors.modules` selects author-owned experiments separately from maintained
laboratory capabilities (#708). Different registered folders may select different
catalogs; revisions retain the exact declaration. Source/refresh boundaries,
package ownership, dependency requirements and lab capabilities remain maintained.
Author-only manifests are now supported for laboratories whose `[lab]` contains
only an installed adapter reference (#710). Registration binds the folder to that
laboratory; revisions capture its adapter declaration and installed artifact
identity. Local driver/bootstrap projects still use the combined declaration.
Environment installation and updates remain separate work.


### Repeatable delivery output

The delivery builder accepts a fixed `--output-home` (#715), retaining every attempt
and selecting only a verified completed artifact. Setup and stopped updates accept
that stable path and pin its selected manifest before installation. This removes
manual output numbering; it does not automatically update a runtime or certify an
installation. Builds remain wheel-based and use ordinary tool caches. Source-aware
artifact reuse and the full checkout/setup flow remain #712 work.


### Registered Notebook entry

`scopecat notebook [WORKSPACE] --home HOME` resolves a bound author folder to the
laboratory's current interpreter (#717). It preflights Notebook extras, uses a
per-session kernelspec and does not start an experiment service. Relaunch after a
stopped environment update to select the replacement interpreter. Existing kernels
and external editors are not redirected; close them before updating. This is a
foreground local entry, not manager-owned Notebook lifecycle supervision.

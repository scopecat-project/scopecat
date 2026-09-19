# Platform status and remaining work

This is the current integration map, including source selection (#657) and the
bounded Help/navigation slice (#658), rather than another historical roadmap. The umbrella is [#610](https://github.com/scopecat-project/scopecat/issues/610).
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
| Application entry | Registered workbenches and lifecycle operations (#627, #633); workbench Help and manager-preserving navigation (#658) | Host still supervises separate services; not shared cross-service physical authority |
| Author/results | Direct callable compute, inferred scalar outputs, dict returns and validated native historical result rows (#584, #586) | Symbolic argument static typing remains broader than native calls |
| Grouped analysis | Request sweeps and retained completed-run group analysis | No durable live-group completion/scheduling protocol |
| Data recovery | Current-format backup/restore and non-mutating rejection of unsupported formats | No supported persistent baseline or selected-run exchange format |

See [configuration ownership](architecture/configuration-ownership.md),
[workspace bindings](architecture/workspace-bindings.md),
[target execution](architecture/target-execution.md) and
[application host](architecture/application-host.md) for the actual contracts.

## Near-term sequence

1. **Reconcile consumers and maintenance entry points** under
   [#615](https://github.com/scopecat-project/scopecat/issues/615).
   The launcher audit is tracked as [#659](https://github.com/scopecat-project/scopecat/issues/659):
   retire redundant single-run adapters, reuse checked catalogs, then narrow the
   baseline-less worker contract while retaining real multi-stage workflows.
   Bounded Help/navigation is delivered by [#658](https://github.com/scopecat-project/scopecat/issues/658).
   The remaining installation pairing and environment maintenance must keep
   registration, execution and scientific context as separate actions.
   Record concrete replacement coverage before removing any reference fixture.
2. **Qualify the resulting local application milestone** under
   [#616](https://github.com/scopecat-project/scopecat/issues/616).
   Exercise installed Windows/Linux startup/update/restart, notebook/browser source
   flows, current-format recovery and failure cleanup. Do not wait for every future
   platform feature. Record software evidence separately from hardware or human use.
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

Same-environment source ownership is delivered; [#613](https://github.com/scopecat-project/scopecat/issues/613)
remains for environment/installation boundaries and qualification. The first app
slice closed #614 prematurely; it is reopened with the remaining local integration
scope, separately from completed entry/lifecycle controls.

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

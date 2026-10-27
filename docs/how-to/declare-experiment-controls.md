# Declare experiment controls once

Ordinary authors should start with [input-owned control metadata](managed-author-session.md#declare-controls-next-to-their-inputs).
This page covers the explicit maintained control contract, ownership and project validators.

`sc.Control` describes a maintained numeric control: its default, unit, bounds,
label, group, ownership, and provenance. Attach its `sc.ControlSet` explicitly
with `@sc.experiment(controls=controls)`. It supplies the same defaults to
immutable Python invocations and the typed launch catalog. For example, with an
explicitly resolved `config` from your author session:

```python
import scopecat as sc

FREQUENCY = sc.Control("frequency", default=sc.Quantity(4.8, "GHz"), scannable=True)
CONTROLS = sc.ControlSet((FREQUENCY,))


@sc.experiment(controls=CONTROLS)
def controlled(context: sc.ExperimentContext):
    return FREQUENCY.ref


original = controlled.build()
fixed = CONTROLS.apply(
    original, config=config, edits={"frequency": sc.Quantity(4900, "MHz")}
)
scanned = CONTROLS.apply(
    fixed,
    config=config,
    edits={"frequency": sc.axis(FREQUENCY.ref, [sc.Quantity(4.9, "GHz")])},
)
restored_default = CONTROLS.apply(scanned, config=config, reset=("frequency",))
```

A scannable control has one coordinate/axis source. A scalar edit creates a
single-value `AxisSpec` with `mode="fixed"`; an explicit axis has `mode="scan"`,
even when it contains one point. Replacing that axis removes its old values.
Returning to a scalar requires a value or the declared default; the prior scalar
is not kept behind a scan. Each edit returns a new invocation. Persisted run
requests include fixed mode; historical scan records omit the default scan mode
and retain their existing JSON/content identity.

For a non-scannable control, declare an ordinary `sc.Input` of the matching type
and put its default only on the `Control`. Use `experiment.bind()` to construct
an invocation using those defaults, or bind explicit scalar inputs as usual.
The declaration rejects a mismatched input type or a duplicate function default.
Real scalar and real `Quantity` controls are supported; this is not a general
form schema or a new scan language. Forms support existing values and range
axes, without overlays or around-config axes.

## Project validation and ownership

A `ControlSet` may receive a project-owned validator taking
`sc.ControlValidationContext`. Its invocation, frozen configuration and axis
specifications describe the edited plan. The validator runs after control edits
and again in the normal planning/admission entry, including direct `.with_axis`
and `.bind` edits. Validate per-axis bounds or extrema and cross-field rules;
do not enumerate a Cartesian scan merely to validate it.

A configuration-owned control can read a value from the explicitly selected
parameter revision; a derived control can compute an offset from that value and
the edited axis. The shared `testing/fixtures/retained-signal` example uses one
signal center for this purpose. Both values display their provenance. Resolver
outputs are normalized and checked against declared units/bounds before display;
they cannot be edited through the control interface. Project-specific limits are
not global framework scan limits.

For an ordinary one-experiment author path, the [discovery adapter](write-an-experiment.md)
automatically supplies these launch projections and single-run execution,
including a declared control validator. Maintained multi-stage workflows can use
the same helpers inside their explicit provider.

## How discovery uses the declaration

`control_catalog(controls)` produces `LaunchCatalogEntry.controls`.
`edit_controls(controls, invocation, config=..., edits=request.control_edits)`
converts the typed `ControlEdit` source into the same immutable edit operations.
`control_values(...)` supplies `LaunchPreview.controls` with explicit
fixed/scanned/derived/configuration states and provenance. Authored discovery
uses all three with the normal scientific binding and configuration fences,
manual preview checks, and managed procedure admission; single-run experiments
do not need a separate launch adapter.

A `ControlEdit` chooses exactly one source: `fixed` plus `value`, `scan` plus an
existing axis record, or `default` with neither. Unknown or owned fields are
rejected. The common launch worker validates nonempty edits against the project
catalog before calling its action; direct Python provider implementations must
call `validate_launch_control_edits`. Empty edits
retain the existing request hash for providers without controls.

The GUI renders labels/groups, declared bounds, readonly ownership and resolved
provenance from these models. A source edit invalidates the old preview and
submission hash. Switching linear units converts the active values; nonlinear
or unknown units retain only their declared unit. The server remains the
unit/constraint authority. No device or accepted configuration is modified by
previewing an experiment.

## Keep a launch draft while inspecting the project

GUI 实验输入按注册作者工作区、实验和草稿保存在应用数据中。确认已保存的字段、控制项
取值来源和单位、科学选择、操作人及记录集合可在导航、页面刷新或应用重启后恢复。
尚未完成的无效文本也会保留；保存输入不等于验证通过。这与 Notebook kernel 中临时的
`session.selection` 不同，详见[选择实验上下文](select-session-context.md)。

关闭前应等待“实验输入已保存到应用数据。”。未确认或失败的保存不能承诺在进程丢失后
恢复，请保持窗口打开并重试。跨窗口编辑冲突会保留独立副本，需要明确选择并保存；
恢复不会静默覆盖另一窗口的已保存输入。

声明变化会保留改动供核实。新字段采用当前默认值；移除或不再可编辑的字段在明确移除前
仍需处理。重置实验草稿会保存采用声明默认值的新修订，早期版本和冲突副本仍在输入历史中。
恢复输入始终需要核实及重新准备，不恢复执行许可。比较已提交请求 A 与当前草稿 B 仅作
描述性展示，不预览、不执行，也不表示 B 就是最初提交的草稿；默认值和未完整输入不会判同。

Input edits, changed declarations, and changed parameter/setup revisions
invalidate the preview. A temporary failure to read configuration blocks
submission until the context can be verified without discarding retained inputs.

If a submission response is lost, its original payload and request key remain
separate from the editable draft. **核实原始提交** reads retained
procedures by that key; **查看已提交任务** opens the confirmed ID and
does not dispatch it. Confirmation requires one matching procedure whose public
launch intent retains the exact `request_hash` and `config_source` binding from
the request. Providers without this evidence, missing matches, or multiple
matches remain explicitly unconfirmed. An explicit retry uses the original
payload and key only while its declaration and configuration are still verified.
Changing the context or resetting inputs never silently resubmits an unknown
request or replaces its pending-confirmation entry.

# Record a human or AI experiment decision

Use a procedure interpretation step when the next experiment action depends on
a scientific judgment that is not reliable enough to hard-code. Typical cases
include selecting real resonators from a broad S21 scan, mapping a resonator to
a qubit from a bias sweep, choosing a working point, or rejecting a fit whose
numeric diagnostics miss an obvious artifact.

Define the response as an ordinary stable analysis-fact schema. Keep measured
evidence in run or analysis records and put only presentation hints in request
metadata.

```python
from dataclasses import dataclass

from scopecat import AnalysisFactSchema
from scopecat.api.procedures import LabProcedureContext


@dataclass(frozen=True)
class ResonatorSelection:
    resonator: str
    center_frequency_hz: float
    confidence: float
    rationale: str


RESONATOR_SELECTION = AnalysisFactSchema(
    "my-lab.resonator-selection.v1",
    ResonatorSelection,
)


def continue_survey(
    context: LabProcedureContext,
    survey_run,
) -> None:
    decision = context.interpret(
        "select-resonator",
        title="Select a physical readout resonator",
        instructions=(
            "Inspect the broad S21 trace. Exclude cable ripple and duplicated "
            "fit candidates; return the selected physical resonator."
        ),
        schema=RESONATOR_SELECTION,
        inputs=(survey_run,),
        response_template=ResonatorSelection(
            resonator="replace after reviewing the trace",
            center_frequency_hz=0.0,
            confidence=0.0,
            rationale="replace with the experimental judgment",
        ),
        metadata={"preferred_view": "readout-s21"},
    )

    # This line runs only after an exact response has been recorded and the
    # procedure has been resumed. `decision.ref` carries durable provenance.
    selected = decision.value
    run_follow_up(
        context,
        resonator=selected.resonator,
        center_frequency_hz=selected.center_frequency_hz,
        inputs=(survey_run, decision.ref),
    )
```

The first execution stops in `waiting_for_input` and releases its worker lease.
Open **Decisions** in the project console, inspect the request and its named
evidence, identify the judging human, AI, or service, and record JSON matching
the displayed structure. An optional `response_template` is validated against
that structure and becomes the editor's starting value; use it to reduce
transcription without presenting it as an accepted decision. The recorded actor
is provenance supplied by the answering client, rather than an authenticated
account identity. The same operation is available from Python:

```python
procedure = lab.procedures.get(procedure_run_id)
procedure.respond(
    "select-resonator",
    ResonatorSelection(
        resonator="r2",
        center_frequency_hz=6.713e9,
        confidence=0.86,
        rationale="isolated dip with the expected bias response",
    ),
    schema=RESONATOR_SELECTION,
    actor="operator@example.org",
    actor_kind="human",  # also "ai" or "service"
    note="checked against the bias sweep",
)
```

Recording the response returns the procedure to `ready` but does not execute
the next experiment in that HTTP request. Without a resident project worker,
resume after checking that the next device action is appropriate:

```python
procedure.resume()
```

A configured resident project worker can claim the ready procedure on its next
poll. In that mode, recording the decision also authorizes the already-declared
continuation; pause the resident worker first when an additional manual hold is
required.

Use `attention_required` instead when a device or publication outcome is
unknown and must be reconciled. Do not turn an expected scientific choice into
an execution failure merely to pause the procedure.


## Recover unfinished edits

The Decisions editor automatically saves reviewer, reasoning, JSON/form mode and
raw field text in application data, including incomplete numbers and invalid JSON.
Wait for **Draft saved in application data** before quitting. A pending or failed
save is not a durability confirmation. Editing saves never record a scientific
decision, apply calibration or start work.

The last confirmed draft is restored after navigating away, opening another
window or restarting with a different service port. Another data home has its own
drafts. If two windows edit the same request, a conflicting copy is retained in
history and recording is blocked in that editor. **Use my copy as current draft**
is an explicit conditional replacement; another concurrent change can conflict
again. Neither copy is deleted.

**Recover saved Decision drafts** is available even when no task is waiting.
It shows retained revisions, including completed requests and conflicting or
explicitly discarded copies, for inspection and copying. History cannot submit a
decision. A changed baseline blocks recording; review the current request and
use **Discard draft (keep recovery history)** to start a fresh draft, then
explicitly copy and revalidate any text you want to reuse. Discard clears only
that request's current draft; it does not erase retained history or scientific
records. There is no automatic draft expiry or history deletion.

This does not import old browser-local drafts or repair the separate native Mac
website-store clearing problem.

## Retain decisions as analysis inputs

Pass the exact `decision.ref` to a project analysis and consume it with
`AnalysisContext.interpretation`. Passing only `decision.value` supplies a value
but does not establish a decision provenance edge.

```python
from scopecat.automation import InterpretationOutputRef


@sc.analysis_step(id="lab.selection-report.v1")
def selection_report(
    analysis: sc.AnalysisContext,
    *,
    selection: InterpretationOutputRef,
) -> sc.Analysis:
    value = analysis.interpretation(selection, schema=RESONATOR_SELECTION)
    return analysis.result("Selected resonator").fact(
        "selection", value, schema=RESONATOR_SELECTION
    )


# Inside the procedure, after context.interpret(...):
report = context.analyze_project(
    "selection-report",
    selection_report(selection=decision.ref),
    inputs=(decision.ref,),
)
```

The publication retains procedure ID, step key, request hash and a hash of the
complete response, including actor, timestamp and note. The daemon checks these
against an existing successful interpretation step before publishing. Unknown or
altered references are rejected. The analysis input table displays the exact
procedure and step; the values remain in the durable procedure journal.

Direct interpretation inputs currently belong to **project analyses**. Run and
sample analysis subjects do not yet accept them. These references do not add an
authentication mechanism or authorize a new hardware operation.

## Inspect a bounded procedure summary

```python
summary = procedure.summary(limit=20)
print(summary.outcome, summary.next_action, summary.reason)
for step in summary.steps:
    print(step.step_key, step.state, step.inputs, step.output)
    if step.interpretation_request is not None:
        print(step.interpretation_request)
```

`outcome` exposes `succeeded`, `failed` or `cancelled` for a closed procedure,
instead of reporting only `closed`. `next_action` is `respond`, `resume`,
`inspect_attention`, `wait` or `none`; `reason` includes failure/attention detail.
The method reads one snapshot and one newest-first step page. `next_cursor` means
older attempts are omitted, not that the displayed count is the procedure total.
Use `procedure.steps(before=summary.next_cursor)` to inspect more history.

This is an observational view: the two reads are not atomic, and a concurrent
worker may advance the procedure. Mutating commands still use their existing
revision and lease checks.

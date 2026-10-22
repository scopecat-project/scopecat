# Manual physical-line confirmation

The finite manual-line workflow uses stable line IDs independently of scientific
subject, target and setup identity. Ordinary acquisition parameters may vary
between explicitly prepared acquisitions. Each child retains its original
source, configuration, operation ID and run admission. Labels are not sample IDs.

## Shared boundary with drain and recovery

This slice owns only explicitly declared manual-line acquisitions. Drain and
ordinary admission remain owned by the existing lifecycle services (#1097).
The shared interface is reviewed against #1159 at
`eca0e6c78fd5d6fa9906aff1ad3117350cb2e864`: an expired manual confirmation
remains an attention blocker. Human reconfirmation/continue is an ordinary
mutation, so the user must cancel exit wait first. The existing HTTP attention
endpoint stays outside the drain allowlist. Necessary admitted child execution
may reach the freshness fence while draining; this does not grant new human
continuation. Final integration must be checked on main after #1159 merges. No exit state or second execution-state store is introduced.

`LabProcedureContext.run(..., manual_line_id=...)` plans the frozen acquisition
before asking for a human wiring confirmation through the existing interpretation
step. The confirmation identifies the physical line, child step and acquisition
intent hash. The child retains that exact interpretation as an input. A different
acquisition cannot reuse the answer.

The server records its process instance with the first answer. Idempotent answer
retries return the original evidence, including after restart. This is freshness
evidence, not authentication, permission or a physical wiring detector.

Before the **first** executor lease, the server checks that confirmation in the
same transaction as lease admission. An expired confirmation on an unstarted
queued child becomes `attention_required` with reason
`manual_line_confirmation_expired`. No execution segment or unknown device effect
is invented. The parent uses its existing step-attention path.

The existing attention continuation command accepts an explicit
`manual_line_reconfirmation` only for this unstarted case. It names the original
confirmation and a stable request key, with the new human actor. The existing
`run_continuation_authorized` event retains that evidence and the current server
instance in the same transaction as attention-to-queued. Original admission,
source, configuration, line and operation identity remain unchanged. A lost
response retries the same command; reusing its key never renews freshness on a
later server instance. After another restart a new explicit confirmation is
needed. The receipt acknowledges the historical command, so clients refresh
current state rather than treating a replay receipt as permission to execute.

Runs with execution segments retain the existing external-state reconciliation
contract. Wiring confirmation cannot resolve their uncertainty. Ordinary runs
without a manual-line declaration retain existing behavior. Completed evidence
is restored without acquiring another lease.

The server-side confirmation and continuation protocol is a prerequisite for the
finite line-list UI; it is not itself delivery of the full persistent line list,
attempt history and comparison experience in #1023. Acceptance uses fake devices.

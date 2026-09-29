# `scopecat_quantum.authoring`

The authoring facade provides hardware-independent quantum definitions,
programs, pulses, binding, and inspection. Target integration contracts remain
in their explicit owner modules.

Use `Qubit` for an identified logical object and `ReadoutLine` for a readout
path whose sample association may still be unknown. Both support `readout()`
and `acquire()`; logical gates and `measure()` continue to operate on qubits.

```python
from scopecat import Quantity
from scopecat_quantum import authoring as q


@q.program
def survey(line: q.ReadoutLine):
    return q.parallel(
        q.play(
            q.readout(line),
            q.constant(
                duration=Quantity(100, "ns"),
                amplitude=Quantity(0.1, "arb"),
            ),
        ),
        q.acquire(line, duration=Quantity(100, "ns"), result="iq"),
    )
```

Bind `line` to an entity of kind `readout_line`. Target adapters map its
`ReadoutSignal(ReadoutLineId(...))` and `AcquireSignal(ReadoutLineId(...))` to
physical channels. Signals and authored measurement results expose `owner`;
the same text ID used for a qubit remains a different identity. A survey result
does not itself establish which sample object produced a resonance.

::: scopecat_quantum.authoring
    options:
      filters:
        - "!^_"
      show_bases: false
      show_root_full_path: false
      show_signature_annotations: true

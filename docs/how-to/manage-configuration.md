# Review and publish project configuration

A project's `src/<package>/configuration.py` is ordinary version-controlled
Python. The daemon owns the accepted configuration history; it does not watch or
rewrite that source file.

Validate the source without starting the daemon:

```sh
scopecat config check ./my-lab
```

With the project daemon running, compare a freshly evaluated source snapshot
with the current daemon default:

```sh
scopecat config diff ./my-lab
```

Review the diff, then explicitly publish it with an operator identity and useful
audit note:

```sh
scopecat config apply ./my-lab \
  --actor alice \
  --note "add readout VNA and reviewed defaults"
```

Export a complete JSON snapshot for review or backup:

```sh
scopecat config export ./my-lab --output ./active-config.json
```

The exported JSON is generated state, not the primary editing format. Continue
editing the project's Python configuration source and use `diff` and `apply` for
subsequent changes.


## Propose an entity or field edit

Use an existing typed cell or keyed row update for calibration changes. For
example, the public reference lab proposes one delay with
`Q1_CHANNEL_CALIBRATION[CHANNEL_DELAY].update(1.0)`. The general form is:

```python
edit = sc.update_parameter_rows(
    "channels",
    key={"qubit": sc.EntityRef(id="q1", kind="qubit")},
    values={"frequency": sc.Quantity(5100, "MHz")},
)
analysis.result().propose("q1-frequency", edit, reason="reviewed fit")
```

The catalog validates the key, fields and compatible quantity dimensions. A
candidate retains explicitly supplied quantity units, and leaves other cells'
representations unchanged. Execution resolves quantities into catalog units
transiently. Whole-table replacement remains available for deliberate structural
changes; use cell/row updates when only a calibration field is intended.

New proposal deltas retain entity/key, field, original base and proposed values.
Decision and configuration views distinguish a **physical value change** from an
**equivalent representation**: changing `5 GHz` to `5000 MHz` is still an explicit
reviewable edit, even though its physical value is equal. Older publications
remain readable and show their retained before/after values.

Common-base proposals can compose independent cells. Identical changes to one
cell coalesce. Different representations of that cell produce a representation
conflict even when physically equivalent; different physical values produce a
physical conflict. Both report the original base and competing values. Neither
selects an automatic winner. Unchanged cells and incidental table normalization
do not become calibration changes.

Acceptance continues to require the frozen expected registry generation and
retains proposal provenance. Undo appends an activation of the previous exact
entry; it does not erase acceptance history. Refresh and review again after a
generation conflict rather than silently accepting against a newer base.

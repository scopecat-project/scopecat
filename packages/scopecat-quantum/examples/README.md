# Editing a pulse recipe

Run `uv run python packages/scopecat-quantum/examples/quantity_recipe.py` from
the repository root. This device-free example expands the same X90 helper at
two duration points and prints its waveform parameters and schedule. Change
the phase offset or Gaussian width expression and run it again. The existing
program lowering, scheduling and inspection contracts are used unchanged.

Annotate quantity-valued recipe ports as
`Annotated[q.QuantumQuantity, QuantityType(unit="ns")]`. `QuantumQuantity`
means a concrete `Quantity`, an input handle, or a `QuantityExpression`.
It does not promise an already available float. Ordinary undecorated helpers
can use `q.QuantumQuantity` directly.

Supported expressions are `duration / 4`, `0.9 * amplitude`, `-phase`,
`phase + Quantity(90, "deg")`, and compatible quantity subtraction, including
reversed operand order. Use explicit units for offsets; `duration + 4` is
ambiguous and rejected. Multiplying two symbolic quantities, powers and
arbitrary numerical functions are outside this bounded expression language.
Construct expressions with these operators, rather than instantiating the
expression carrier yourself. Their values are computed when each program
point binds, before concrete target IR is produced. Normal pulse validation
still checks the resulting duration, amplitude and envelope.

Keep separately calibrated pi and half-pi amplitudes as separate parameters.
Arithmetic convenience provides no scientific justification for deriving one
from the other.

In a running author project, save helper edits, refresh the author revision,
and preview again before submitting a new run. Already admitted runs keep
their source revision. `examples/reference_lab/tests/test_author_refresh.py`
checks revision retention through worker execution and store restoration;
`tests/test_quantity_expressions.py` checks the concrete waveform and timing
of nested expressions at multiple points. The standalone example above does
not launch a worker or refresh a running project.

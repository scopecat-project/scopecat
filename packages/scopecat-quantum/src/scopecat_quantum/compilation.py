"""Recipe-backed compilation shared by hardware target adapters."""

from collections.abc import Mapping
from dataclasses import dataclass

from scopecat_quantum import authoring as quantum
from scopecat_quantum._ids import PulseProgramId, TargetCompileEntryId
from scopecat_quantum.inspection import (
    QuantumProgramInspectionSnapshot,
    build_quantum_program_inspection_snapshot,
)
from scopecat_quantum.program_targets import (
    PreparedQuantumTargetEntry,
    prepare_quantum_target_entry,
)
from scopecat_quantum.programs import plan_quantum_pulse_lowering
from scopecat_quantum.pulse_recipes import (
    PulseRecipeMaterializationCache,
    PulseRecipeProfile,
)
from scopecat_quantum.realtime import ScheduledBlock


@dataclass(frozen=True, slots=True)
class CompiledRecipeEntry:
    """Logical evidence and target-ready program from the same compilation."""

    bound: quantum.BoundProgram
    entry: PreparedQuantumTargetEntry
    inspection: QuantumProgramInspectionSnapshot | None


class RecipeTargetCompiler[ParametersT]:
    """Compile points against one recipe profile and selected parameter snapshot.

    Create one instance per compilation batch. Parameters must remain unchanged
    during its lifetime, including named scoped snapshots. Scopes select gate
    recipe rows only; measurement recipes continue to use the baseline.
    Device encoding, payload limits and execution stay with
    the target adapter; recipe resolution and lowering stay in the framework.
    """

    def __init__(
        self,
        profile: PulseRecipeProfile[ParametersT],
        parameters: ParametersT,
        *,
        max_expanded_operations: int | None = None,
        scoped_parameters: Mapping[str, ParametersT] | None = None,
    ) -> None:
        self._profile = profile
        self._parameters = parameters
        self._scoped_parameters = dict(scoped_parameters or {})
        self._max_expanded_operations = max_expanded_operations
        self._cache = PulseRecipeMaterializationCache()

    def compile(
        self,
        program: quantum.Program,
        bindings: Mapping[str, object],
        *,
        entry_id: TargetCompileEntryId,
        inspect: bool = False,
    ) -> CompiledRecipeEntry:
        """Bind, resolve recipes and lower one point with a common work budget."""
        bound = quantum.bind(program, bindings)
        implementations = self._profile.materialize_quantum(
            self._parameters,
            bound.verified,
            cache=self._cache,
            scoped_parameters=self._scoped_parameters,
            max_expanded_operations=self._max_expanded_operations,
        )
        entry = prepare_quantum_target_entry(
            entry_id,
            plan_quantum_pulse_lowering(
                bound.verified,
                implementations,
                output_id=PulseProgramId(f"{entry_id.value}.pulses"),
                max_expanded_operations=self._max_expanded_operations,
            ),
        )
        inspection = None
        if inspect:
            body = entry.program.body
            inspection = build_quantum_program_inspection_snapshot(
                program,
                bound=bound,
                scheduled=body.program if isinstance(body, ScheduledBlock) else None,
                snapshot_id=entry_id.value,
            )
        return CompiledRecipeEntry(bound, entry, inspection)

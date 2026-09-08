"""Immutable experiment definitions and invocation values."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Generic, Self, TypeVar, cast

from scopecat.adaptive_domains import AdaptiveScope
from scopecat.kernel.frozen import FrozenMapping, freeze_json_mapping
from scopecat.kernel.value_types import ValueType
from scopecat.optimization import AdaptiveDomainPlan, DomainOptimizer
from scopecat.program.bindings import (
    BindingIntent,
    EnsureStateIntent,
)
from scopecat.program.input_capture import capture_runtime_inputs, empty_program_mapping
from scopecat.program.measurement_types import (
    MeasurementArrayData,
    MeasurementSegmentedData,
)
from scopecat.program.module import (
    ModuleBody,
    ModuleInterface,
    ModulePythonImplementation,
)
from scopecat.program.products import ProductRef
from scopecat.program.record_refs import RecordRef
from scopecat.program.recording import ExperimentResultField, ProgramRecordSelection
from scopecat.program.scans import (
    AxisSpec,
    GridSpec,
    PointGrouping,
    PointPlan,
    PointRow,
    PointsSpec,
    PointTraversal,
    RepeatMode,
    points_spec,
)
from scopecat.program.value_refs import (
    CoordinateRef,
    ValueRef,
    internal_coordinate_ref_id,
    internal_value_ref_point_id,
)
from scopecat.program.values import (
    MetadataValue,
    RuntimeInput,
)
from scopecat.program.verification import (
    validate_experiment_definition,
    validate_experiment_inputs,
)

if TYPE_CHECKING:
    from scopecat.program.control_contract import InvocationControls


class _InputDefaultMissing:
    __slots__ = ()


_INPUT_DEFAULT_MISSING = _InputDefaultMissing()

_ExperimentResultT_co = TypeVar(
    "_ExperimentResultT_co",
    covariant=True,
    default=object,
)
type ExperimentResultRef = ProductRef | RecordRef | ValueRef


def _empty_result_refs() -> Mapping[tuple[str, ...], ExperimentResultRef]:
    return FrozenMapping()


@dataclass(frozen=True, slots=True)
class ExperimentInputDef:
    """One normalized experiment input consumed by binding and compilation."""

    id: str
    value_type: ValueType | None
    required: bool = False
    default: RuntimeInput | _InputDefaultMissing = _INPUT_DEFAULT_MISSING

    @property
    def has_default(self) -> bool:
        return self.default is not _INPUT_DEFAULT_MISSING


@dataclass(frozen=True, slots=True)
class ExperimentDef:
    """Canonical config-free root and policy shared by every invocation path.

    Unlike a reusable :class:`ModuleDef`, the root body may consume experiment
    coordinates and parameter expressions directly.
    """

    id: str
    kind: str
    interface: ModuleInterface
    body: ModuleBody
    python_implementations: tuple[ModulePythonImplementation, ...] = ()
    inputs: tuple[ExperimentInputDef, ...] = ()
    default_point_plan: PointPlan = field(default_factory=PointPlan)
    controls: InvocationControls | None = field(default=None, repr=False, compare=False)
    record_selections: tuple[ProgramRecordSelection, ...] = ()
    result_fields: tuple[ExperimentResultField, ...] = ()
    success_state: EnsureStateIntent | None = None
    metadata: Mapping[str, MetadataValue] = field(default_factory=empty_program_mapping)

    def __post_init__(self) -> None:
        product_ids = tuple(
            product.qualified_id for product in self.body.exposed_products
        )
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("experiment definition contains duplicate product ids")
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))


@dataclass(frozen=True, slots=True, repr=False)
class ExperimentInvocation(Generic[_ExperimentResultT_co]):
    """One invocation with typed authored output and its durable record projection."""

    definition: ExperimentDef
    input_overrides: Mapping[str, RuntimeInput] = field(
        default_factory=empty_program_mapping
    )
    point_plan_override: PointPlan | None = None
    adaptive_domain_plan: AdaptiveDomainPlan | None = field(
        default=None,
        repr=False,
    )
    output: _ExperimentResultT_co = field(
        kw_only=True,
        repr=False,
        compare=False,
    )
    recorded_result_refs: Mapping[tuple[str, ...], ExperimentResultRef] = field(
        default_factory=_empty_result_refs,
        kw_only=True,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "recorded_result_refs",
            FrozenMapping(self.recorded_result_refs.items()),
        )

    def result_ref(self, path: str | Sequence[str], /) -> ExperimentResultRef:
        """Return one durable result handle by dotted or segmented result path."""

        selected = tuple(path.split(".")) if isinstance(path, str) else tuple(path)
        try:
            return self.recorded_result_refs[selected]
        except KeyError as error:
            raise KeyError(
                f"experiment result has no path {'.'.join(selected)!r}"
            ) from error

    def entity_result_ref(
        self,
        path: str | Sequence[str],
        /,
    ) -> RecordRef[MeasurementArrayData | MeasurementSegmentedData]:
        """Return one entity-axis array result with its static array type."""

        ref = self.result_ref(path)
        if not isinstance(ref, RecordRef) or ref.entity_axis_id is None:
            raise TypeError(f"experiment result {path!r} is not an entity-axis record")
        return cast("RecordRef[MeasurementArrayData | MeasurementSegmentedData]", ref)

    @property
    def point_plan(self) -> PointPlan:
        """Return the invocation override or the definition's complete plan."""

        return self.point_plan_override or self.definition.default_point_plan

    def bind(self, **inputs: RuntimeInput) -> Self:
        captured_inputs = capture_experiment_inputs(inputs)
        validate_experiment_inputs(
            definitions=self.definition.inputs,
            inputs=captured_inputs,
        )
        selected = dict(self.input_overrides)
        selected.update(captured_inputs)
        return replace(
            self,
            input_overrides=FrozenMapping(selected.items()),
        )

    def adaptive(
        self,
        optimizer: DomainOptimizer,
        *,
        max_points: int,
        axes: Sequence[CoordinateRef | str] = (),
        scope: AdaptiveScope = "per_region",
        per_region_max_points: int | None = None,
    ) -> Self:
        """Extend selected coordinates with compatible optimizer domains."""

        return replace(
            self,
            adaptive_domain_plan=AdaptiveDomainPlan(
                optimizer=optimizer,
                total_point_limit=max_points,
                adaptive_coordinate_ids=tuple(_axis_target_id(axis) for axis in axes),
                scope=scope,
                per_region_point_limit=per_region_max_points,
            ),
        )

    def without_adaptation(self) -> Self:
        """Retain the authored point plan but remove its optimizer policy."""

        return replace(self, adaptive_domain_plan=None)

    def unbind(self, *input_ids: str) -> Self:
        """Remove invocation overrides so definition defaults apply again."""

        allowed = {definition.id for definition in self.definition.inputs}
        unknown = sorted(set(input_ids) - allowed)
        if unknown:
            raise ValueError("experiment received unknown input: " + ", ".join(unknown))
        selected = dict(self.input_overrides)
        for input_id in input_ids:
            selected.pop(input_id, None)
        return replace(
            self,
            input_overrides=FrozenMapping(selected.items()),
        )

    def grid(self, *axes: AxisSpec) -> Self:
        """Replace the complete point domain with a Cartesian grid."""

        return replace(
            self,
            point_plan_override=replace(
                self.point_plan,
                domain=GridSpec(tuple(axes)),
            ),
        )

    def points(
        self,
        rows: Sequence[PointRow],
        *,
        coordinates: Sequence[CoordinateRef] = (),
    ) -> Self:
        """Replace the complete point domain with ordered explicit points."""

        return replace(
            self,
            point_plan_override=replace(
                self.point_plan,
                domain=points_spec(rows, coordinates=coordinates),
                schedule=replace(self.point_plan.schedule, traversal="forward"),
            ),
        )

    def reset_points(self) -> Self:
        """Discard the complete point-plan override and inherit the definition."""

        return replace(self, point_plan_override=None)

    def with_repeat(
        self,
        count: int,
        *,
        mode: RepeatMode = "point",
    ) -> Self:
        """Replace point- or sweep-repeat policy without changing the domain."""

        return replace(
            self,
            point_plan_override=replace(
                self.point_plan,
                repeat=count,
                repeat_mode=mode,
            ),
        )

    def with_traversal(
        self,
        traversal: PointTraversal,
    ) -> Self:
        """Replace physical traversal policy without changing logical points."""

        return replace(
            self,
            point_plan_override=replace(
                self.point_plan,
                schedule=replace(self.point_plan.schedule, traversal=traversal),
            ),
        )

    def with_point_grouping(
        self,
        id: str,
        *,
        varying: Sequence[CoordinateRef],
    ) -> Self:
        """Prefer related points together and restart an interrupted group."""

        return replace(
            self,
            point_plan_override=replace(
                self.point_plan,
                schedule=replace(
                    self.point_plan.schedule,
                    grouping=PointGrouping(
                        id=id,
                        varying_coordinate_ids=tuple(
                            internal_coordinate_ref_id(coordinate)
                            for coordinate in varying
                        ),
                    ),
                ),
            ),
        )

    def without_point_grouping(self) -> Self:
        """Remove the scheduling and interruption grouping policy."""

        return replace(
            self,
            point_plan_override=replace(
                self.point_plan,
                schedule=replace(self.point_plan.schedule, grouping=None),
            ),
        )

    def with_axis(self, axis: AxisSpec) -> Self:
        """Replace one grid axis in place, or append it when newly introduced."""

        domain = self.point_plan.domain
        if isinstance(domain, PointsSpec):
            raise TypeError("point clouds do not support incremental grid axes")
        selected = tuple(
            axis if existing.id == axis.id else existing for existing in domain.axes
        )
        if all(existing.id != axis.id for existing in domain.axes):
            selected = (*selected, axis)
        return replace(
            self,
            point_plan_override=replace(
                self.point_plan,
                domain=GridSpec(selected),
            ),
        )

    def without_axis(self, target: ValueRef | str) -> Self:
        """Remove one named grid axis while preserving the remaining order."""

        domain = self.point_plan.domain
        if isinstance(domain, PointsSpec):
            raise TypeError("point clouds do not support incremental grid axes")
        axis_id = _axis_target_id(target)
        if all(axis.id != axis_id for axis in domain.axes):
            raise ValueError(f"grid has no axis {axis_id!r}")
        return replace(
            self,
            point_plan_override=replace(
                self.point_plan,
                domain=GridSpec(
                    tuple(axis for axis in domain.axes if axis.id != axis_id)
                ),
            ),
        )


def create_experiment_def(
    *,
    id: str,
    kind: str,
    interface: ModuleInterface,
    body: ModuleBody,
    python_implementations: Sequence[ModulePythonImplementation] = (),
    record_selections: Sequence[ProgramRecordSelection] = (),
    result_fields: Sequence[ExperimentResultField] = (),
    input_defaults: Mapping[str, RuntimeInput] | None = None,
    required_inputs: Sequence[str] = (),
    default_point_plan: PointPlan | None = None,
    controls: InvocationControls | None = None,
    success_state_bindings: Sequence[BindingIntent] = (),
    metadata: Mapping[str, MetadataValue] | None = None,
) -> ExperimentDef:
    """Normalize all experiment semantics at one immutable boundary."""

    if not id:
        msg = "experiment definition id must be non-empty"
        raise ValueError(msg)
    if not kind:
        msg = "experiment definition requires kind"
        raise ValueError(msg)
    selected_records = tuple(record_selections)
    selected_result_fields = tuple(result_fields)
    result_paths = tuple(field.path for field in selected_result_fields)
    if len(result_paths) != len(set(result_paths)):
        raise ValueError("experiment result paths must be unique")
    selected_point_plan = (
        default_point_plan if default_point_plan is not None else PointPlan()
    )
    selected_defaults = capture_experiment_inputs(input_defaults or {})
    selected_required = tuple(required_inputs)
    input_types = validate_experiment_definition(
        input_ports=interface.imports,
        defaults=selected_defaults,
        default_point_plan=selected_point_plan,
    )
    program_input_ids = tuple(port.id for port in interface.imports)
    input_ids = tuple(
        dict.fromkeys(
            (
                *program_input_ids,
                *selected_defaults,
                *selected_required,
                *input_types,
            )
        )
    )
    normalized_inputs = tuple(
        ExperimentInputDef(
            id=input_id,
            value_type=input_types.get(input_id),
            required=input_id in selected_required,
            default=selected_defaults.get(input_id, _INPUT_DEFAULT_MISSING),
        )
        for input_id in input_ids
    )
    return ExperimentDef(
        id=id,
        kind=kind,
        interface=interface,
        body=body,
        python_implementations=tuple(python_implementations),
        inputs=normalized_inputs,
        default_point_plan=selected_point_plan,
        controls=controls,
        record_selections=selected_records,
        result_fields=selected_result_fields,
        success_state=(
            EnsureStateIntent(tuple(success_state_bindings))
            if success_state_bindings
            else None
        ),
        metadata=freeze_json_mapping(metadata or {}),
    )


def capture_experiment_inputs(
    inputs: Mapping[str, RuntimeInput],
) -> Mapping[str, RuntimeInput]:
    try:
        captured = capture_runtime_inputs(cast("Mapping[str, object]", inputs))
    except (TypeError, ValueError) as error:
        selected = ", ".join(repr(input_id) for input_id in sorted(inputs))
        msg = (
            "experiment inputs require non-empty names and closed runtime data: "
            f"{selected}"
        )
        raise TypeError(msg) from error
    return cast("Mapping[str, RuntimeInput]", captured)


def _axis_target_id(target: ValueRef | str) -> str:
    if isinstance(target, str):
        if not target:
            raise ValueError("grid axis id must be non-empty")
        return target
    axis_id = internal_value_ref_point_id(target)
    if axis_id is None:
        raise TypeError("grid axis targets must be created with scopecat.coordinate")
    return axis_id

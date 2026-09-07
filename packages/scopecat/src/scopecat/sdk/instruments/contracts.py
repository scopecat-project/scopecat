"""Instrument driver SDK."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

import scopecat.sdk.instruments.commands as _commands
from scopecat.kernel.content_identity import model_wire_content_hash
from scopecat.kernel.instrument_members import (
    DevicePropertyRef,
    DeviceSchemaId,
    PropertyRef,
    StateMemberRef,
)
from scopecat.kernel.interface_identity import InterfaceId
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.state import PayloadRef, StateValue
from scopecat.kernel.units import compatible_units
from scopecat.kernel.value_identity import scalar_values_equal
from scopecat.kernel.value_type_compatibility import is_assignable
from scopecat.kernel.value_type_wire import (
    InstrumentOperationScalarWire,
    InstrumentPropertyScalarWire,
)
from scopecat.kernel.value_types import Bool as BoolType
from scopecat.kernel.value_types import Float as FloatType
from scopecat.kernel.value_types import Int as IntType
from scopecat.kernel.value_types import Payload as PayloadType
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.kernel.value_types import Scalar
from scopecat.kernel.value_types import String as StringType
from scopecat.kernel.value_validation import (
    ValuePath,
    ValueValidationError,
    coerce_literal,
    validate_literal,
)
from scopecat.measurements.contracts import (
    measurement_value_contract_issues,
)
from scopecat.program.measurement_types import (
    MeasurementDType,
    MeasurementVariableRole,
)
from scopecat.records.instrument import (
    InstrumentStateObservation as _InstrumentStateObservation,
)
from scopecat.records.instrument import (
    InstrumentStateSnapshot as _InstrumentStateSnapshot,
)
from scopecat.records.instrument import (
    InterfaceStateMemberTarget as _InterfaceStateMemberTarget,
)
from scopecat.records.instrument import (
    StateMemberTarget as _StateMemberTarget,
)
from scopecat.records.instrument import (
    state_member_identity as _state_member_identity,
)
from scopecat.records.instrument import (
    state_member_sort_key as _state_member_sort_key,
)
from scopecat.records.instrument import (
    state_member_target as _state_member_target,
)
from scopecat.sdk.instruments.authoring import DriverStateReadRequest
from scopecat.sdk.instruments.projection import (
    ProjectedInstrumentState as _ProjectedInstrumentState,
)
from scopecat.sdk.problems import (
    LocationPathItem,
    ModelLocation,
    Problem,
    ProblemPhase,
    model_location,
    problem,
)

type _NonEmptyId = Annotated[str, Field(min_length=1)]
type PropertyAccess = Literal["read_only", "write_only", "read_write"]
_JSON_SAFE_INTEGER = (1 << 53) - 1


def _exclude_none(value: object) -> bool:
    return value is None


def _validate_property_lifecycle(
    access: PropertyAccess,
    *,
    capture: bool,
    restore: bool,
) -> None:
    if capture and access == "write_only":
        raise ValueError("write-only properties cannot be captured")
    if restore and access != "read_write":
        raise ValueError("restorable properties must be readable and writable")
    if restore and not capture:
        raise ValueError("restorable properties must be captured")


class PropertySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: _NonEmptyId
    label: str | None = None
    description: str | None = None
    access: PropertyAccess = "read_write"
    capture: bool = True
    restore: bool = False
    value_type: InstrumentPropertyScalarWire

    @field_validator("value_type")
    @classmethod
    def validate_value_type(cls, value: Scalar) -> Scalar:
        return _validate_instrument_scalar(value, allow_payload=False)

    @model_validator(mode="after")
    def validate_lifecycle_policy(self) -> PropertySpec:
        _validate_property_lifecycle(
            self.access,
            capture=self.capture,
            restore=self.restore,
        )
        return self


class StatePropertyRef(BaseModel):
    """One persistent property referenced by an instrument contract."""

    model_config = ConfigDict(extra="forbid")

    interface_id: InterfaceId
    component_path: list[_NonEmptyId] = Field(default_factory=list)
    property_id: _NonEmptyId


class InterfacePropertyImplementationSpec(BaseModel):
    """Concrete semantics for one portable property at a physical endpoint.

    Omitted properties retain their interface contract semantics. Implementations
    may narrow access, lifecycle participation, or admitted values, but never
    widen the portable contract.
    """

    model_config = ConfigDict(extra="forbid")

    property: StatePropertyRef
    access: PropertyAccess
    capture: bool
    restore: bool
    value_type: InstrumentPropertyScalarWire | None = Field(
        default=None,
        exclude_if=_exclude_none,
    )

    @model_validator(mode="after")
    def validate_lifecycle_policy(self) -> InterfacePropertyImplementationSpec:
        _validate_property_lifecycle(
            self.access,
            capture=self.capture,
            restore=self.restore,
        )
        return self


type AcquisitionAxisSize = (
    Annotated[int, Field(strict=True, ge=1, le=_JSON_SAFE_INTEGER)]
    | StatePropertyRef
    | None
)


class LinearCoordinatesSpec(BaseModel):
    """Expected coordinates sampled uniformly between two state values."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["linear"] = "linear"
    start: StatePropertyRef
    stop: StatePropertyRef
    endpoint: bool = True


class AcquisitionAxisSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: _NonEmptyId
    label: str | None = None
    description: str | None = None
    kind: _NonEmptyId
    size: AcquisitionAxisSize
    unit: str | None = None
    coordinate_result: _NonEmptyId | None = Field(
        default=None,
        exclude_if=_exclude_none,
    )
    coordinates: LinearCoordinatesSpec | None = Field(
        default=None,
        exclude_if=_exclude_none,
    )


class AcquisitionResultSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: _NonEmptyId
    label: str | None = None
    description: str | None = None
    role: MeasurementVariableRole = "observable"
    dtype: MeasurementDType = "float64"
    unit: str | None = None
    axes: list[AcquisitionAxisSpec] = Field(default_factory=list)
    source_property: StatePropertyRef | None = Field(
        default=None,
        exclude_if=_exclude_none,
    )

    @field_validator("axes")
    @classmethod
    def validate_unique_axes(
        cls,
        value: list[AcquisitionAxisSpec],
    ) -> list[AcquisitionAxisSpec]:
        _require_unique(
            (axis.id for axis in value),
            "acquisition result axis ids",
        )
        return value

    @model_validator(mode="after")
    def validate_unitless_dtype(self) -> AcquisitionResultSpec:
        if self.dtype in {"bool", "string"} and self.unit is not None:
            raise ValueError(f"{self.dtype} acquisition results cannot have a unit")
        return self


class AcquisitionPreconditionSpec(BaseModel):
    """One public state value required before an acquisition can start."""

    model_config = ConfigDict(extra="forbid")

    property: StatePropertyRef
    value: bool | int | float | str | Quantity
    unavailable_reason: _NonEmptyId

    @model_validator(mode="after")
    def validate_finite_value(self) -> AcquisitionPreconditionSpec:
        value = self.value
        if (isinstance(value, float) and not math.isfinite(value)) or (
            isinstance(value, Quantity) and not math.isfinite(value.value)
        ):
            raise ValueError("acquisition precondition values must be finite")
        return self


class OperationArgumentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: _NonEmptyId
    label: str | None = None
    description: str | None = None
    value_type: InstrumentOperationScalarWire

    @field_validator("value_type")
    @classmethod
    def validate_value_type(cls, value: Scalar) -> Scalar:
        return _validate_instrument_scalar(value, allow_payload=True)


class OperationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: _NonEmptyId
    label: str | None = None
    description: str | None = None
    arguments: list[OperationArgumentSpec] = Field(default_factory=list)
    invalidates: list[StatePropertyRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_members(self) -> OperationSpec:
        _require_unique(
            (argument.id for argument in self.arguments),
            "operation argument ids",
        )
        _require_unique(
            (
                f"{reference.interface_id}:"
                f"{'/'.join(reference.component_path)}:{reference.property_id}"
                for reference in self.invalidates
            ),
            "operation invalidation references",
        )
        return self


class AcquisitionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: _NonEmptyId
    label: str | None = None
    description: str | None = None
    preconditions: list[AcquisitionPreconditionSpec] = Field(default_factory=list)
    results: list[AcquisitionResultSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_results(self) -> AcquisitionSpec:
        _require_unique(
            (result.id for result in self.results),
            "acquisition result ids",
        )
        results_by_id = {result.id: result for result in self.results}
        axes_by_id = {axis.id: axis for result in self.results for axis in result.axes}
        for axis in axes_by_id.values():
            coordinate_result_id = axis.coordinate_result
            if coordinate_result_id is None:
                if axis.coordinates is not None:
                    raise ValueError(
                        f"acquisition axis {axis.id!r} declares expected coordinates "
                        "without a coordinate result"
                    )
                continue
            coordinate = results_by_id.get(coordinate_result_id)
            if coordinate is None:
                raise ValueError(
                    f"acquisition axis {axis.id!r} references unknown coordinate "
                    f"result {coordinate_result_id!r}"
                )
            if coordinate.role != "coordinate":
                raise ValueError(
                    f"acquisition axis {axis.id!r} coordinate result "
                    f"{coordinate_result_id!r} must have the coordinate role"
                )
            if len(coordinate.axes) != 1 or coordinate.axes[0].id != axis.id:
                raise ValueError(
                    f"acquisition axis {axis.id!r} coordinate result "
                    f"{coordinate_result_id!r} must vary only along that axis"
                )
            if coordinate.unit != axis.unit:
                raise ValueError(
                    f"acquisition axis {axis.id!r} and coordinate result "
                    f"{coordinate_result_id!r} must use the same unit"
                )
        return self


@dataclass(frozen=True, slots=True)
class AcquisitionReadinessIssue:
    kind: Literal["state_unknown", "precondition_not_met"]
    reason: str
    precondition: AcquisitionPreconditionSpec
    resolved_property: StatePropertyRef | None = None
    observed_value: StateValue | None = None


@dataclass(frozen=True, slots=True)
class AcquisitionReadiness:
    status: Literal["ready", "blocked", "unknown"]
    issues: tuple[AcquisitionReadinessIssue, ...] = ()


class ComponentSpec(BaseModel):
    """One stable role nested below an interface endpoint."""

    model_config = ConfigDict(extra="forbid")

    id: _NonEmptyId
    label: str | None = None
    description: str | None = None
    properties: list[PropertySpec] = Field(default_factory=list)
    operations: list[OperationSpec] = Field(default_factory=list)
    acquisitions: list[AcquisitionSpec] = Field(default_factory=list)
    components: list[ComponentSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_members(self) -> ComponentSpec:
        _validate_component_members(self)
        return self


class InterfaceSpec(BaseModel):
    """Versioned behavior contract implemented by an instrument endpoint."""

    model_config = ConfigDict(extra="forbid")

    id: InterfaceId
    label: str | None = None
    description: str | None = None
    properties: list[PropertySpec] = Field(default_factory=list)
    operations: list[OperationSpec] = Field(default_factory=list)
    acquisitions: list[AcquisitionSpec] = Field(default_factory=list)
    components: list[ComponentSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_members(self) -> InterfaceSpec:
        _validate_component_members(self)
        return self


class InterfaceMountSpec(BaseModel):
    """One physical path where an instrument implements an interface contract."""

    model_config = ConfigDict(extra="forbid")

    interface_id: InterfaceId
    component_path: list[_NonEmptyId] = Field(min_length=1)


class InstrumentComponentSpec(BaseModel):
    """One stable physical component in an instrument-owned topology."""

    model_config = ConfigDict(extra="forbid")

    id: _NonEmptyId
    label: str | None = None
    description: str | None = None
    components: list[InstrumentComponentSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_components(self) -> InstrumentComponentSpec:
        _require_unique(
            (component.id for component in self.components),
            "instrument component ids",
        )
        return self


class DeviceStateMemberSpec(BaseModel):
    """One concrete-model state member at a physical component path."""

    model_config = ConfigDict(extra="forbid")

    component_path: list[_NonEmptyId] = Field(default_factory=list)
    property: PropertySpec


class DeviceStateSpec(BaseModel):
    """Versioned model-specific state that is not a portable capability."""

    model_config = ConfigDict(extra="forbid")

    id: DeviceSchemaId
    label: str | None = None
    description: str | None = None
    members: list[DeviceStateMemberSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_members(self) -> DeviceStateSpec:
        _require_unique(
            (
                f"{'/'.join(member.component_path)}:{member.property.id}"
                for member in self.members
            ),
            "device state member targets",
        )
        return self


class InstrumentDescription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    implementation_id: str
    implementation_version: str
    label: str | None = None
    description: str | None = None
    components: list[InstrumentComponentSpec] = Field(default_factory=list)
    device_schemas: list[DeviceStateSpec] = Field(default_factory=list)
    interfaces: list[InterfaceSpec] = Field(default_factory=list)
    interface_mounts: list[InterfaceMountSpec] = Field(default_factory=list)
    interface_property_implementations: list[InterfacePropertyImplementationSpec] = (
        Field(default_factory=list)
    )

    @model_validator(mode="after")
    def validate_description(self) -> InstrumentDescription:
        # Description normalization must not mutate reusable contract specs.
        self.interfaces = [
            interface_spec.model_copy(deep=True) for interface_spec in self.interfaces
        ]
        self.device_schemas = [
            device_schema.model_copy(deep=True) for device_schema in self.device_schemas
        ]
        self.interface_property_implementations = [
            implementation.model_copy(deep=True)
            for implementation in self.interface_property_implementations
        ]
        _require_unique(
            (interface.id for interface in self.interfaces),
            "instrument interface ids",
        )
        _require_unique(
            (device_schema.id for device_schema in self.device_schemas),
            "instrument device schema ids",
        )
        _require_unique(
            (component.id for component in self.components),
            "instrument component ids",
        )
        known_interface_ids = {interface.id for interface in self.interfaces}
        _require_unique(
            (
                f"{mount.interface_id}:{'/'.join(mount.component_path)}"
                for mount in self.interface_mounts
            ),
            "instrument interface mounts",
        )
        unknown_mounts = sorted(
            {
                mount.interface_id
                for mount in self.interface_mounts
                if mount.interface_id not in known_interface_ids
            }
        )
        if unknown_mounts:
            raise ValueError(
                "instrument interface mounts reference unknown interfaces: "
                f"{', '.join(unknown_mounts)}"
            )
        for mount in self.interface_mounts:
            if _resolve_instrument_component(self, mount.component_path) is None:
                raise ValueError(
                    f"interface {mount.interface_id!r} mount references unknown "
                    f"instrument component {'/'.join(mount.component_path)!r}"
                )
        for device_schema in self.device_schemas:
            for member in device_schema.members:
                if (
                    member.component_path
                    and _resolve_instrument_component(self, member.component_path)
                    is None
                ):
                    raise ValueError(
                        f"device state member {member.property.id!r} references "
                        "unknown instrument component "
                        f"{'/'.join(member.component_path)!r}"
                    )
        for interface_id, mounts in _interface_mounts_by_id(self).items():
            for index, left in enumerate(mounts):
                for right in mounts[index + 1 :]:
                    if _path_prefix(left, right) or _path_prefix(right, left):
                        raise ValueError(
                            f"interface {interface_id!r} mounts must not overlap: "
                            f"{'/'.join(left)!r} and {'/'.join(right)!r}"
                        )
        _require_unique(
            (
                f"{implementation.property.interface_id}:"
                f"{'/'.join(implementation.property.component_path)}:"
                f"{implementation.property.property_id}"
                for implementation in self.interface_property_implementations
            ),
            "instrument interface property implementations",
        )
        for implementation in self.interface_property_implementations:
            declared = _resolve_interface_property_spec(self, implementation.property)
            if declared is None:
                raise ValueError(
                    "interface property implementation references unknown target "
                    f"{implementation.property.interface_id!r}:"
                    f"{'/'.join(implementation.property.component_path)}:"
                    f"{implementation.property.property_id!r}"
                )
            declared_access = _property_access_permissions(declared.access)
            implemented_access = _property_access_permissions(implementation.access)
            if not implemented_access <= declared_access:
                raise ValueError(
                    "interface property implementation cannot widen access for "
                    f"{implementation.property.property_id!r}"
                )
            if implementation.capture and not declared.capture:
                raise ValueError(
                    "interface property implementation cannot enable capture for "
                    f"{implementation.property.property_id!r}"
                )
            if implementation.restore and not declared.restore:
                raise ValueError(
                    "interface property implementation cannot enable restoration for "
                    f"{implementation.property.property_id!r}"
                )
            if implementation.value_type is not None and not is_assignable(
                implementation.value_type,
                declared.value_type,
            ):
                raise ValueError(
                    "interface property implementation cannot widen values for "
                    f"{implementation.property.property_id!r}"
                )
        _validate_contract_state_references(self)
        return self


def interface(
    id: str,
    *,
    label: str | None = None,
    description: str | None = None,
    properties: list[PropertySpec] | tuple[PropertySpec, ...] = (),
    operations: list[OperationSpec] | tuple[OperationSpec, ...] = (),
    acquisitions: list[AcquisitionSpec] | tuple[AcquisitionSpec, ...] = (),
    components: list[ComponentSpec] | tuple[ComponentSpec, ...] = (),
) -> InterfaceSpec:
    return InterfaceSpec(
        id=id,
        label=label,
        description=description,
        properties=list(properties),
        operations=list(operations),
        acquisitions=list(acquisitions),
        components=list(components),
    )


def component(
    id: str,
    *,
    label: str | None = None,
    description: str | None = None,
    properties: list[PropertySpec] | tuple[PropertySpec, ...] = (),
    operations: list[OperationSpec] | tuple[OperationSpec, ...] = (),
    acquisitions: list[AcquisitionSpec] | tuple[AcquisitionSpec, ...] = (),
    components: list[ComponentSpec] | tuple[ComponentSpec, ...] = (),
) -> ComponentSpec:
    return ComponentSpec(
        id=id,
        label=label,
        description=description,
        properties=list(properties),
        operations=list(operations),
        acquisitions=list(acquisitions),
        components=list(components),
    )


def interface_mount(
    interface_id: InterfaceId,
    *component_path: str,
) -> InterfaceMountSpec:
    return InterfaceMountSpec(
        interface_id=interface_id,
        component_path=list(component_path),
    )


def instrument_component(
    id: str,
    *,
    label: str | None = None,
    description: str | None = None,
    components: (
        list[InstrumentComponentSpec] | tuple[InstrumentComponentSpec, ...]
    ) = (),
) -> InstrumentComponentSpec:
    return InstrumentComponentSpec(
        id=id,
        label=label,
        description=description,
        components=list(components),
    )


def acquisition_axis(
    id: str,
    *,
    size: int | PropertyRef | None,
    kind: str | None = None,
    unit: str | None = None,
    coordinate_result: str | None = None,
    coordinates: LinearCoordinatesSpec | None = None,
    label: str | None = None,
    description: str | None = None,
) -> AcquisitionAxisSpec:
    return AcquisitionAxisSpec(
        id=id,
        label=label,
        description=description,
        kind=kind or id,
        size=(
            size if isinstance(size, int) or size is None else _state_property_ref(size)
        ),
        unit=unit,
        coordinate_result=coordinate_result,
        coordinates=coordinates,
    )


def acquisition_result(
    id: str,
    *,
    role: MeasurementVariableRole = "observable",
    dtype: MeasurementDType = "float64",
    unit: str | None = None,
    label: str | None = None,
    description: str | None = None,
    axes: list[AcquisitionAxisSpec] | tuple[AcquisitionAxisSpec, ...] = (),
    source_property: PropertyRef | None = None,
) -> AcquisitionResultSpec:
    return AcquisitionResultSpec(
        id=id,
        label=label,
        description=description,
        role=role,
        dtype=dtype,
        unit=unit,
        axes=list(axes),
        source_property=(
            None if source_property is None else _state_property_ref(source_property)
        ),
    )


def acquisition(
    id: str,
    *,
    label: str | None = None,
    description: str | None = None,
    results: (list[AcquisitionResultSpec] | tuple[AcquisitionResultSpec, ...]) = (),
    preconditions: (
        list[AcquisitionPreconditionSpec] | tuple[AcquisitionPreconditionSpec, ...]
    ) = (),
) -> AcquisitionSpec:
    return AcquisitionSpec(
        id=id,
        label=label,
        description=description,
        results=list(results),
        preconditions=list(preconditions),
    )


def _state_property_ref(property: PropertyRef) -> StatePropertyRef:
    return StatePropertyRef(
        interface_id=property.interface_id,
        component_path=list(property.component_path),
        property_id=property.property_id,
    )


def acquisition_precondition(
    property: PropertyRef,
    *,
    value: bool | int | float | str | Quantity,  # noqa: PYI041
    unavailable_reason: str,
) -> AcquisitionPreconditionSpec:
    return AcquisitionPreconditionSpec(
        property=_state_property_ref(property),
        value=value,
        unavailable_reason=unavailable_reason,
    )


def resolve_acquisition_dimensions(
    *,
    description: InstrumentDescription,
    result: AcquisitionResultSpec,
    state: _InstrumentStateSnapshot | None,
    interface_id: InterfaceId | None = None,
    component_path: Sequence[str] = (),
) -> tuple[_commands.CollectAxisRequest, ...] | None:
    """Resolve every contract-owned axis size from one synchronized snapshot."""

    return _resolve_acquisition_dimensions(
        description=description,
        result=result,
        state=state,
        interface_id=interface_id,
        component_path=component_path,
    )


def _resolve_acquisition_dimensions(
    *,
    description: InstrumentDescription,
    result: AcquisitionResultSpec,
    state: _InstrumentStateSnapshot | _ProjectedInstrumentState | None,
    interface_id: InterfaceId | None = None,
    component_path: Sequence[str] = (),
) -> tuple[_commands.CollectAxisRequest, ...] | None:
    axes = result.axes
    if not axes:
        return ()
    if any(isinstance(axis.size, StatePropertyRef) for axis in axes) and not (
        _acquisition_state_is_usable(state, description)
    ):
        return None

    dimensions: list[_commands.CollectAxisRequest] = []
    for axis in axes:
        if isinstance(axis.size, StatePropertyRef):
            reference = _physical_state_reference(
                description,
                axis.size,
                context_interface_id=interface_id,
                context_component_path=component_path,
            )
            if reference is None:
                return None
            assert state is not None
            value = _state_value_for_reference(state, reference)
            if (
                value is None
                or isinstance(value.root, bool)
                or not isinstance(value.root, int)
                or value.root < 1
            ):
                return None
            size = value.root
        else:
            size = axis.size
        dimensions.append(
            _commands.CollectAxisRequest(
                id=axis.id,
                kind=axis.kind,
                size=size,
                unit=axis.unit,
            )
        )
    return tuple(dimensions)


def evaluate_acquisition_readiness(
    *,
    description: InstrumentDescription,
    acquisition: AcquisitionSpec,
    state: _InstrumentStateSnapshot | None,
    interface_id: InterfaceId | None = None,
    component_path: Sequence[str] = (),
) -> AcquisitionReadiness:
    """Evaluate public acquisition state without touching hardware."""

    return _evaluate_acquisition_readiness(
        description=description,
        acquisition=acquisition,
        state=state,
        interface_id=interface_id,
        component_path=component_path,
    )


def _evaluate_acquisition_readiness(
    *,
    description: InstrumentDescription,
    acquisition: AcquisitionSpec,
    state: _InstrumentStateSnapshot | _ProjectedInstrumentState | None,
    interface_id: InterfaceId | None = None,
    component_path: Sequence[str] = (),
) -> AcquisitionReadiness:
    issues = _acquisition_precondition_issues(
        description=description,
        preconditions=acquisition.preconditions,
        state=state,
        interface_id=interface_id,
        component_path=component_path,
    )

    status: Literal["ready", "blocked", "unknown"]
    if any(issue.kind == "precondition_not_met" for issue in issues):
        status = "blocked"
    elif issues:
        status = "unknown"
    else:
        status = "ready"
    return AcquisitionReadiness(
        status=status,
        issues=tuple(issues),
    )


def _acquisition_precondition_issues(
    *,
    description: InstrumentDescription,
    preconditions: Sequence[AcquisitionPreconditionSpec],
    state: _InstrumentStateSnapshot | _ProjectedInstrumentState | None,
    interface_id: InterfaceId | None,
    component_path: Sequence[str],
) -> list[AcquisitionReadinessIssue]:
    if not preconditions:
        return []
    resolved_references = [
        _physical_state_reference(
            description,
            precondition.property,
            context_interface_id=interface_id,
            context_component_path=component_path,
        )
        for precondition in preconditions
    ]
    if not _acquisition_state_is_usable(state, description):
        return [
            AcquisitionReadinessIssue(
                kind="state_unknown",
                reason=precondition.unavailable_reason,
                precondition=precondition,
                resolved_property=reference,
            )
            for precondition, reference in zip(
                preconditions,
                resolved_references,
                strict=True,
            )
        ]

    assert state is not None
    issues: list[AcquisitionReadinessIssue] = []
    for precondition, reference in zip(
        preconditions,
        resolved_references,
        strict=True,
    ):
        observed = (
            None if reference is None else _state_value_for_reference(state, reference)
        )
        if observed is None:
            issues.append(
                AcquisitionReadinessIssue(
                    kind="state_unknown",
                    reason=precondition.unavailable_reason,
                    precondition=precondition,
                    resolved_property=reference,
                )
            )
            continue
        resolved = _resolve_state_property(description, precondition.property)
        assert resolved is not None
        _, property_spec = resolved
        if not _precondition_matches(
            precondition,
            observed=observed,
            property_spec=property_spec,
        ):
            issues.append(
                AcquisitionReadinessIssue(
                    kind="precondition_not_met",
                    reason=precondition.unavailable_reason,
                    precondition=precondition,
                    resolved_property=reference,
                    observed_value=observed,
                )
            )
    return issues


def _acquisition_state_is_usable(
    state: _InstrumentStateSnapshot | _ProjectedInstrumentState | None,
    description: InstrumentDescription,
) -> bool:
    if state is None or state.instrument_id != description.instrument_id:
        return False
    return isinstance(state, _ProjectedInstrumentState) or not validate_state_snapshot(
        snapshot=state,
        description=description,
    )


def _state_value_for_reference(
    state: _InstrumentStateSnapshot | _ProjectedInstrumentState,
    reference: StatePropertyRef,
) -> StateValue | None:
    identity = _state_member_identity(
        PropertyRef(
            reference.interface_id,
            tuple(reference.component_path),
            reference.property_id,
        )
    )
    return next(
        (
            observation.value
            for observation in state.observations
            if _state_member_identity(observation.target) == identity
        ),
        None,
    )


def state_assignment_satisfied(
    state: _InstrumentStateSnapshot,
    assignment: _commands.InstrumentStateAssignment,
) -> bool:
    """Return whether observed state semantically satisfies one assignment."""

    identity = _state_member_identity(assignment.target)
    actual = next(
        (
            item.value
            for item in state.observations
            if _state_member_identity(item.target) == identity
        ),
        None,
    )
    return actual is not None and scalar_values_equal(
        actual.root,
        assignment.value.root,
    )


def _precondition_matches(
    precondition: AcquisitionPreconditionSpec,
    *,
    observed: StateValue,
    property_spec: PropertySpec,
) -> bool:
    left = coerce_literal(property_spec.value_type, observed.root)
    right = coerce_literal(property_spec.value_type, precondition.value)
    return scalar_values_equal(left, right)


def operation_argument(
    id: str,
    *,
    value_type: InstrumentOperationScalarWire,
    label: str | None = None,
    description: str | None = None,
) -> OperationArgumentSpec:
    return OperationArgumentSpec(
        id=id,
        label=label,
        description=description,
        value_type=value_type,
    )


def operation(
    id: str,
    *,
    label: str | None = None,
    description: str | None = None,
    arguments: (list[OperationArgumentSpec] | tuple[OperationArgumentSpec, ...]) = (),
    invalidates: Sequence[PropertyRef] = (),
) -> OperationSpec:
    """Declare an imperative operation and the state knowledge it withdraws.

    Each entry in ``invalidates`` names a property whose prior observed or
    requested value cannot be trusted after the operation. Invalidation does
    not assert a replacement value; a later readback or write establishes new
    evidence.
    """

    return OperationSpec(
        id=id,
        label=label,
        description=description,
        arguments=list(arguments),
        invalidates=[_state_property_ref(property) for property in invalidates],
    )


def _capture_default(
    access: Literal["read_only", "write_only", "read_write"],
    capture: bool | None,
) -> bool:
    return access != "write_only" if capture is None else capture


def quantity_property(
    id: str,
    *,
    unit: str,
    minimum: float | None = None,
    maximum: float | None = None,
    label: str | None = None,
    description: str | None = None,
    access: Literal["read_only", "write_only", "read_write"] = "read_write",
    capture: bool | None = None,
    restore: bool = False,
) -> PropertySpec:
    return PropertySpec(
        id=id,
        label=label,
        description=description,
        access=access,
        capture=_capture_default(access, capture),
        restore=restore,
        value_type=Scalar(
            QuantityType(
                unit=unit,
                minimum=minimum,
                maximum=maximum,
            )
        ),
    )


def bool_property(
    id: str,
    *,
    label: str | None = None,
    description: str | None = None,
    access: Literal["read_only", "write_only", "read_write"] = "read_write",
    capture: bool | None = None,
    restore: bool = False,
) -> PropertySpec:
    return PropertySpec(
        id=id,
        label=label,
        description=description,
        access=access,
        capture=_capture_default(access, capture),
        restore=restore,
        value_type=Scalar(BoolType()),
    )


def int_property(
    id: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    label: str | None = None,
    description: str | None = None,
    access: Literal["read_only", "write_only", "read_write"] = "read_write",
    capture: bool | None = None,
    restore: bool = False,
) -> PropertySpec:
    return PropertySpec(
        id=id,
        label=label,
        description=description,
        access=access,
        capture=_capture_default(access, capture),
        restore=restore,
        value_type=Scalar(IntType(minimum=minimum, maximum=maximum)),
    )


def float_property(
    id: str,
    *,
    label: str | None = None,
    description: str | None = None,
    access: Literal["read_only", "write_only", "read_write"] = "read_write",
    capture: bool | None = None,
    restore: bool = False,
) -> PropertySpec:
    return PropertySpec(
        id=id,
        label=label,
        description=description,
        access=access,
        capture=_capture_default(access, capture),
        restore=restore,
        value_type=Scalar(FloatType()),
    )


def string_property(
    id: str,
    *,
    choices: tuple[str, ...] | None = None,
    label: str | None = None,
    description: str | None = None,
    access: Literal["read_only", "write_only", "read_write"] = "read_write",
    capture: bool | None = None,
    restore: bool = False,
) -> PropertySpec:
    return PropertySpec(
        id=id,
        label=label,
        description=description,
        access=access,
        capture=_capture_default(access, capture),
        restore=restore,
        value_type=Scalar(StringType(choices=choices)),
    )


def enum_property(
    id: str,
    *,
    choices: tuple[str, ...],
    label: str | None = None,
    description: str | None = None,
    access: Literal["read_only", "write_only", "read_write"] = "read_write",
    capture: bool | None = None,
    restore: bool = False,
) -> PropertySpec:
    return string_property(
        id,
        choices=choices,
        label=label,
        description=description,
        access=access,
        capture=capture,
        restore=restore,
    )


def validate_state_command(
    *,
    command: _commands.InstrumentStateCommand,
    description: InstrumentDescription,
    baseline: _InstrumentStateSnapshot | _ProjectedInstrumentState | None = None,
) -> list[Problem]:
    """Validate one structurally complete command against a driver contract."""

    return validate_state_assignments(
        instrument_id=command.instrument_id,
        assignments=command.assignments,
        description=description,
        baseline=baseline,
    )


def validate_state_assignments(
    *,
    instrument_id: str,
    assignments: Sequence[_commands.InstrumentStateAssignment],
    description: InstrumentDescription,
    baseline: _InstrumentStateSnapshot | _ProjectedInstrumentState | None = None,
) -> list[Problem]:
    """Validate persistent property assignments against one driver contract."""

    return _validate_state_assignments(
        instrument_id=instrument_id,
        assignments=assignments,
        description=description,
        baseline=baseline,
        require_observable=False,
    )


def validate_reconciled_state_assignments(
    *,
    instrument_id: str,
    assignments: Sequence[_commands.InstrumentStateAssignment],
    description: InstrumentDescription,
    baseline: _InstrumentStateSnapshot | _ProjectedInstrumentState | None = None,
) -> list[Problem]:
    """Validate desired state that must be observable after it is applied."""

    return _validate_state_assignments(
        instrument_id=instrument_id,
        assignments=assignments,
        description=description,
        baseline=baseline,
        require_observable=True,
    )


def _validate_state_assignments(
    *,
    instrument_id: str,
    assignments: Sequence[_commands.InstrumentStateAssignment],
    description: InstrumentDescription,
    baseline: _InstrumentStateSnapshot | _ProjectedInstrumentState | None,
    require_observable: bool,
) -> list[Problem]:
    problems: list[Problem] = []
    if instrument_id != description.instrument_id:
        problems.append(
            _problem(
                "instrument_driver_mismatch",
                f"{description.instrument_id} cannot apply command for {instrument_id}",
                "instrument_id",
            )
        )
        return problems
    if baseline is not None and baseline.instrument_id != instrument_id:
        problems.append(
            _problem(
                "instrument_driver_baseline_mismatch",
                f"{instrument_id} cannot use state observed from "
                f"{baseline.instrument_id}",
                "instrument_id",
            )
        )
        return problems
    if isinstance(baseline, _InstrumentStateSnapshot):
        baseline_problems = validate_state_snapshot(
            snapshot=baseline,
            description=description,
        )
        if baseline_problems:
            return baseline_problems
    for assignment_index, assignment in enumerate(assignments):
        assignment_path: tuple[LocationPathItem, ...] = (
            "assignments",
            assignment_index,
        )
        if assignment.resource_id != instrument_id:
            problems.append(
                _problem(
                    "instrument_driver_resource_mismatch",
                    f"{instrument_id} cannot control {assignment.resource_id}",
                    *assignment_path,
                    "resource_id",
                )
            )
            continue
        property_spec = resolve_state_member_spec(description, assignment.target)
        if property_spec is None:
            problems.append(
                _problem(
                    "instrument_driver_unsupported_member",
                    f"{instrument_id} does not support "
                    f"{_state_member_identity(assignment.target)!r}",
                    *assignment_path,
                    "target",
                )
            )
            continue
        if property_spec.access == "read_only":
            problems.append(
                _problem(
                    "instrument_driver_read_only_property",
                    f"{instrument_id} property {property_spec.id} is read-only",
                    *assignment_path,
                    "target",
                )
            )
            continue
        if require_observable and property_spec.access == "write_only":
            problems.append(
                _problem(
                    "instrument_driver_write_only_property",
                    f"{instrument_id} property {property_spec.id} "
                    "is write-only and cannot be reconciled",
                    *assignment_path,
                    "target",
                )
            )
            continue
        problems.extend(
            _validate_state_value(
                property_id=property_spec.id,
                value=assignment.value,
                spec=property_spec,
                assignment_path=assignment_path,
            )
        )
    return problems


def validate_state_snapshot(
    *,
    snapshot: _InstrumentStateSnapshot,
    description: InstrumentDescription,
) -> list[Problem]:
    """Validate every observation without imposing capture completeness."""

    if snapshot.instrument_id != description.instrument_id:
        return [
            _snapshot_problem(
                "instrument_driver_snapshot_mismatch",
                f"{description.instrument_id} returned state for "
                f"{snapshot.instrument_id}",
                "instrument_id",
            )
        ]
    problems: list[Problem] = []
    for observation_index, observation in enumerate(snapshot.observations):
        property_spec = resolve_state_member_spec(description, observation.target)
        if property_spec is None:
            identity = _state_member_identity(observation.target)
            problems.append(
                _snapshot_problem(
                    "instrument_driver_snapshot_unsupported_member",
                    f"{snapshot.instrument_id} returned unsupported member "
                    f"{identity!r}",
                    "observations",
                    observation_index,
                    "target",
                )
            )
            continue
        if (
            property_spec.access == "write_only"
            and observation.source != "command_confirmed"
        ):
            problems.append(
                _snapshot_problem(
                    "instrument_driver_snapshot_write_only_property",
                    f"{snapshot.instrument_id} returned write-only property "
                    f"{property_spec.id}",
                    "observations",
                    observation_index,
                    "target",
                )
            )
            continue
        try:
            validate_literal(
                property_spec.value_type,
                observation.value.root,
                path=("value",),
            )
        except ValueValidationError as error:
            problems.append(
                _snapshot_problem(
                    "instrument_driver_snapshot_property_value_mismatch",
                    f"{property_spec.id}: {error.reason}",
                    "observations",
                    observation_index,
                    *error.path,
                )
            )
        else:
            atom = property_spec.value_type.atom
            literal = observation.value.root
            # Canonical snapshot units keep browser and worker comparisons identical.
            if (
                isinstance(atom, QuantityType)
                and atom.unit is not None
                and isinstance(literal, Quantity)
                and literal.unit != atom.unit
            ):
                problems.append(
                    _snapshot_problem(
                        "instrument_driver_snapshot_property_value_mismatch",
                        f"{property_spec.id}: observed quantity must use "
                        f"canonical unit {atom.unit!r}",
                        "observations",
                        observation_index,
                        "value",
                        "unit",
                    )
                )
    return problems


def validate_state_capture(
    *,
    snapshot: _InstrumentStateSnapshot,
    description: InstrumentDescription,
    required: Sequence[StateMemberRef] | None = None,
) -> list[Problem]:
    """Validate one lifecycle capture against an explicit member plan."""

    problems = validate_state_snapshot(snapshot=snapshot, description=description)
    expected = tuple(
        capture_state_members(description) if required is None else required
    )
    observed = {
        _state_member_identity(observation.target)
        for observation in snapshot.observations
    }
    missing = tuple(
        target for target in expected if _state_member_identity(target) not in observed
    )
    if missing:
        problems.append(
            _snapshot_problem(
                "instrument_driver_snapshot_missing_members",
                f"{snapshot.instrument_id} capture is missing members "
                f"{[repr(target) for target in missing]!r}",
                "observations",
                details={
                    "member_targets": [repr(target) for target in missing],
                },
            )
        )
    return problems


def validate_invoke_command(
    *,
    command: _commands.InvokeCommand,
    description: InstrumentDescription,
) -> list[Problem]:
    """Validate one atomic operation invocation against a driver contract."""

    if command.instrument_id != description.instrument_id:
        return [
            _invoke_problem(
                "instrument_driver_mismatch",
                f"{description.instrument_id} cannot invoke for "
                f"{command.instrument_id}",
                "instrument_id",
            )
        ]
    if command.resource_id != command.instrument_id:
        return [
            _invoke_problem(
                "instrument_driver_resource_mismatch",
                f"{command.instrument_id} cannot control {command.resource_id}",
                "resource_id",
            )
        ]
    interface_spec = next(
        (
            interface
            for interface in description.interfaces
            if interface.id == command.interface_id
        ),
        None,
    )
    if interface_spec is None:
        return [
            _invoke_problem(
                "instrument_driver_unsupported_interface",
                f"{command.instrument_id} does not support {command.interface_id}",
                "interface_id",
            )
        ]
    component_spec = resolve_implementation_component(
        description,
        interface_spec,
        command.component_path,
    )
    if component_spec is None:
        return [
            _invoke_problem(
                "instrument_driver_unsupported_component",
                f"{command.instrument_id} does not support component "
                f"{'/'.join(command.component_path)!r} under {command.interface_id}",
                "component_path",
            )
        ]
    operation_spec = next(
        (
            operation
            for operation in component_spec.operations
            if operation.id == command.operation_id
        ),
        None,
    )
    if operation_spec is None:
        return [
            _invoke_problem(
                "instrument_driver_unsupported_operation",
                f"{command.instrument_id} does not support operation "
                f"{command.operation_id} under {command.interface_id}",
                "operation_id",
            )
        ]

    declared = {argument.id: argument for argument in operation_spec.arguments}
    supplied = {argument.id: argument for argument in command.arguments}
    problems: list[Problem] = []
    for argument_id in sorted(declared.keys() - supplied.keys()):
        problems.append(
            _invoke_problem(
                "instrument_driver_missing_operation_argument",
                f"{command.instrument_id} operation {command.operation_id} "
                f"requires argument {argument_id}",
                "arguments",
                argument_id,
            )
        )
    for argument_id in sorted(supplied.keys() - declared.keys()):
        problems.append(
            _invoke_problem(
                "instrument_driver_unsupported_operation_argument",
                f"{command.instrument_id} operation {command.operation_id} "
                f"does not accept argument {argument_id}",
                "arguments",
                argument_id,
            )
        )
    payload_schemas = {
        payload_id: payload.schema_id
        for payload_id, payload in command.payloads.items()
    }
    for argument_id in sorted(declared.keys() & supplied.keys()):
        problems.extend(
            _validate_operation_argument(
                argument_id=argument_id,
                value=supplied[argument_id].value,
                spec=declared[argument_id],
                payload_schemas=payload_schemas,
            )
        )
    return problems


def resolve_interactive_collect(
    *,
    intent: _commands.InteractiveCollectIntent,
    description: InstrumentDescription,
    state: _InstrumentStateSnapshot,
) -> _commands.InteractiveCollectResolution:
    """Freeze one direct acquisition intent against synchronized hardware state."""

    if intent.instrument_id != description.instrument_id:
        return _commands.RejectedInteractiveCollect(
            problems=(
                _interactive_collect_problem(
                    "instrument_driver_mismatch",
                    f"{description.instrument_id} cannot collect for "
                    f"{intent.instrument_id}",
                    "instrument_id",
                ),
            )
        )
    interface_spec = next(
        (
            interface
            for interface in description.interfaces
            if interface.id == intent.interface_id
        ),
        None,
    )
    if interface_spec is None:
        return _commands.RejectedInteractiveCollect(
            problems=(
                _interactive_collect_problem(
                    "instrument_driver_unsupported_interface",
                    f"{intent.instrument_id} does not support {intent.interface_id}",
                    "interface_id",
                ),
            )
        )
    component_spec = resolve_implementation_component(
        description,
        interface_spec,
        intent.component_path,
    )
    if component_spec is None:
        return _commands.RejectedInteractiveCollect(
            problems=(
                _interactive_collect_problem(
                    "instrument_driver_unsupported_component",
                    f"{intent.instrument_id} does not support component "
                    f"{'/'.join(intent.component_path)!r}",
                    "component_path",
                ),
            )
        )
    acquisition_spec = next(
        (
            acquisition
            for acquisition in component_spec.acquisitions
            if acquisition.id == intent.acquisition_id
        ),
        None,
    )
    if acquisition_spec is None:
        return _commands.RejectedInteractiveCollect(
            problems=(
                _interactive_collect_problem(
                    "instrument_driver_unsupported_acquisition",
                    f"{intent.instrument_id} does not support acquisition "
                    f"{intent.acquisition_id} under {intent.interface_id}",
                    "acquisition_id",
                ),
            )
        )

    declared_results = {result.id: result for result in acquisition_spec.results}
    unsupported_results = tuple(
        _interactive_collect_problem(
            "instrument_driver_unsupported_acquisition_result",
            f"{intent.instrument_id} acquisition {intent.acquisition_id} "
            f"has no result {result_id}",
            "result_ids",
            result_index,
        )
        for result_index, result_id in enumerate(intent.result_ids)
        if result_id not in declared_results
    )
    if unsupported_results:
        return _commands.RejectedInteractiveCollect(problems=unsupported_results)

    readiness = _evaluate_acquisition_readiness(
        description=description,
        acquisition=acquisition_spec,
        state=state,
        interface_id=intent.interface_id,
        component_path=intent.component_path,
    )
    if readiness.status == "unknown":
        unknown_problems = [
            _interactive_collect_precondition_problem(
                "instrument_driver_acquisition_precondition_state_unknown",
                f"{intent.instrument_id} acquisition "
                f"{intent.acquisition_id} requires synchronized state: "
                f"{issue.reason}",
                issue=issue,
            )
            for issue in readiness.issues
            if issue.kind == "state_unknown"
        ]
        return _commands.RejectedInteractiveCollect(problems=tuple(unknown_problems))

    plan_problems = [
        _interactive_collect_precondition_problem(
            "instrument_driver_acquisition_precondition_not_met",
            f"{intent.instrument_id} acquisition "
            f"{intent.acquisition_id} is unavailable: {issue.reason}",
            issue=issue,
        )
        for issue in readiness.issues
        if issue.kind == "precondition_not_met"
    ]
    if plan_problems:
        return _commands.RejectedInteractiveCollect(problems=tuple(plan_problems))

    selected_result_ids = tuple(intent.result_ids) or tuple(declared_results)
    requests: list[_commands.CollectResultRequest] = []
    dimension_problems: list[Problem] = []
    state_is_usable = _acquisition_state_is_usable(state, description)
    for result_index, result_id in enumerate(selected_result_ids):
        result = declared_results[result_id]
        dimensions = _resolve_acquisition_dimensions(
            description=description,
            result=result,
            state=state,
            interface_id=intent.interface_id,
            component_path=intent.component_path,
        )
        if dimensions is None:
            for axis_index, axis in enumerate(result.axes):
                if not isinstance(axis.size, StatePropertyRef):
                    continue
                reference = _physical_state_reference(
                    description,
                    axis.size,
                    context_interface_id=intent.interface_id,
                    context_component_path=intent.component_path,
                )
                observed = (
                    _state_value_for_reference(state, reference)
                    if state_is_usable and reference is not None
                    else None
                )
                observed_size = (
                    observed.root
                    if observed is not None
                    and not isinstance(observed.root, bool)
                    and isinstance(observed.root, int)
                    and observed.root > 0
                    else None
                )
                if observed_size is None:
                    dimension_problems.append(
                        _interactive_collect_axis_state_problem(
                            intent=intent,
                            result_id=result_id,
                            result_index=(result_index if intent.result_ids else None),
                            axis=axis,
                            reference=reference or axis.size,
                            axis_index=axis_index,
                            observed_value=observed,
                        )
                    )
            continue
        requests.append(
            _commands.CollectResultRequest(
                id=result_id,
                interface_id=intent.interface_id,
                component_path=list(intent.component_path),
                acquisition_id=intent.acquisition_id,
                result_id=result_id,
                unit=result.unit,
                dtype=result.dtype,
                dimensions=list(dimensions),
            )
        )
    if dimension_problems:
        return _commands.RejectedInteractiveCollect(problems=tuple(dimension_problems))
    return _commands.ResolvedInteractiveCollect(
        command=_commands.CollectCommand(
            command_id=intent.command_id,
            instrument_id=intent.instrument_id,
            point_index=0,
            point_count=1,
            requests=requests,
        )
    )


def validate_collect_command(
    *,
    command: _commands.CollectCommand,
    description: InstrumentDescription,
) -> list[Problem]:
    """Validate state-independent acquisition ownership and result contracts."""

    if command.instrument_id != description.instrument_id:
        return [
            _collect_command_problem(
                "instrument_driver_mismatch",
                f"{description.instrument_id} cannot collect for "
                f"{command.instrument_id}",
                "instrument_id",
            )
        ]
    request_target = command.requests[0]
    interface_spec = next(
        (
            interface
            for interface in description.interfaces
            if interface.id == request_target.interface_id
        ),
        None,
    )
    if interface_spec is None:
        return [
            _collect_command_problem(
                "instrument_driver_unsupported_interface",
                f"{command.instrument_id} does not support "
                f"{request_target.interface_id}",
                "requests",
                request_target.id,
                "interface_id",
            )
        ]
    component_spec = resolve_implementation_component(
        description,
        interface_spec,
        request_target.component_path,
    )
    if component_spec is None:
        return [
            _collect_command_problem(
                "instrument_driver_unsupported_component",
                f"{command.instrument_id} does not support component "
                f"{'/'.join(request_target.component_path)!r}",
                "requests",
                request_target.id,
                "component_path",
            )
        ]
    acquisition_spec = next(
        (
            acquisition
            for acquisition in component_spec.acquisitions
            if acquisition.id == request_target.acquisition_id
        ),
        None,
    )
    if acquisition_spec is None:
        return [
            _collect_command_problem(
                "instrument_driver_unsupported_acquisition",
                f"{command.instrument_id} does not support acquisition "
                f"{request_target.acquisition_id} under "
                f"{request_target.interface_id}",
                "requests",
                request_target.id,
                "acquisition_id",
            )
        ]
    declared_results = {result.id: result for result in acquisition_spec.results}
    unsupported_results = [
        _collect_command_problem(
            "instrument_driver_unsupported_acquisition_result",
            f"{command.instrument_id} acquisition "
            f"{request.acquisition_id} has no result {request.result_id}",
            "requests",
            request.id,
            "result_id",
        )
        for request in command.requests
        if request.result_id not in declared_results
    ]
    if unsupported_results:
        return unsupported_results

    problems: list[Problem] = []
    for request in command.requests:
        result_spec = declared_results[request.result_id]
        if request.dtype != result_spec.dtype:
            problems.append(
                _collect_command_problem(
                    "instrument_driver_acquisition_dtype_mismatch",
                    f"{command.instrument_id} acquisition result "
                    f"{request.result_id} requires dtype {result_spec.dtype}, "
                    f"got {request.dtype}",
                    "requests",
                    request.id,
                    "dtype",
                )
            )
        requested_unit = request.unit
        if requested_unit is not None and (
            result_spec.unit is None
            or not compatible_units(requested_unit, result_spec.unit)
        ):
            problems.append(
                _collect_command_problem(
                    "instrument_driver_acquisition_unit_mismatch",
                    f"{command.instrument_id} acquisition result "
                    f"{request.result_id} requires unit compatible with "
                    f"{result_spec.unit!r}, got "
                    f"{requested_unit!r}",
                    "requests",
                    request.id,
                    "unit",
                )
            )
        dimensions = request.dimensions
        if len(dimensions) != len(result_spec.axes):
            problems.append(
                _collect_command_problem(
                    "instrument_driver_acquisition_axes_mismatch",
                    f"{command.instrument_id} acquisition result "
                    f"{request.result_id} axes do not "
                    "match its declared contract",
                    "requests",
                    request.id,
                    "dimensions",
                )
            )
            continue
        for axis_index, (requested_axis, declared_axis) in enumerate(
            zip(dimensions, result_spec.axes, strict=True)
        ):
            if (
                requested_axis.id != declared_axis.id
                or requested_axis.kind != declared_axis.kind
                or (
                    isinstance(declared_axis.size, int)
                    and (
                        (
                            requested_axis.offset is None
                            and requested_axis.size != declared_axis.size
                        )
                        or (
                            requested_axis.offset is not None
                            and (
                                requested_axis.size is None
                                or requested_axis.offset + requested_axis.size
                                > declared_axis.size
                            )
                        )
                    )
                )
            ):
                problems.append(
                    _collect_command_problem(
                        "instrument_driver_acquisition_axis_mismatch",
                        f"{command.instrument_id} acquisition result "
                        f"{request.result_id} axis "
                        f"{declared_axis.id} does not match its declared contract",
                        "requests",
                        request.id,
                        "dimensions",
                        axis_index,
                    )
                )
            requested_axis_unit = requested_axis.unit
            if requested_axis_unit is not None and (
                declared_axis.unit is None
                or not compatible_units(requested_axis_unit, declared_axis.unit)
            ):
                problems.append(
                    _collect_command_problem(
                        "instrument_driver_acquisition_axis_unit_mismatch",
                        f"{command.instrument_id} acquisition result "
                        f"{request.result_id} axis "
                        f"{declared_axis.id} requires a unit compatible with "
                        f"{declared_axis.unit!r}, got {requested_axis_unit!r}",
                        "requests",
                        request.id,
                        "dimensions",
                        axis_index,
                        "unit",
                    )
                )
    return problems


def validate_collect_plan(
    *,
    command: _commands.CollectCommand,
    description: InstrumentDescription,
    baseline: _InstrumentStateSnapshot | _ProjectedInstrumentState | None,
) -> list[Problem]:
    """Check plan coherence; the driver's live guards remain authoritative."""

    problems = validate_collect_command(
        command=command,
        description=description,
    )
    if problems:
        return problems
    request_target = command.requests[0]
    interface_spec = next(
        interface
        for interface in description.interfaces
        if interface.id == request_target.interface_id
    )
    component_spec = resolve_implementation_component(
        description,
        interface_spec,
        request_target.component_path,
    )
    assert component_spec is not None
    acquisition_spec = next(
        acquisition
        for acquisition in component_spec.acquisitions
        if acquisition.id == request_target.acquisition_id
    )
    readiness = _evaluate_acquisition_readiness(
        description=description,
        acquisition=acquisition_spec,
        state=baseline,
        interface_id=request_target.interface_id,
        component_path=request_target.component_path,
    )
    if readiness.status == "unknown":
        unknown_problems = [
            _collect_precondition_problem(
                "instrument_driver_acquisition_precondition_state_unknown",
                f"{command.instrument_id} acquisition "
                f"{request_target.acquisition_id} requires synchronized state: "
                f"{issue.reason}",
                issue=issue,
                request_id=request_target.id,
            )
            for issue in readiness.issues
            if issue.kind == "state_unknown"
        ]
        return unknown_problems

    plan_problems = [
        _collect_precondition_problem(
            "instrument_driver_acquisition_precondition_not_met",
            f"{command.instrument_id} acquisition "
            f"{request_target.acquisition_id} is unavailable: {issue.reason}",
            issue=issue,
            request_id=request_target.id,
        )
        for issue in readiness.issues
        if issue.kind == "precondition_not_met"
    ]
    if plan_problems:
        return plan_problems

    declared_results = {result.id: result for result in acquisition_spec.results}
    state_is_usable = _acquisition_state_is_usable(baseline, description)
    for request in command.requests:
        result = declared_results[request.result_id]
        for axis_index, (requested_axis, declared_axis) in enumerate(
            zip(request.dimensions, result.axes, strict=True)
        ):
            if not isinstance(declared_axis.size, StatePropertyRef):
                continue
            reference = _physical_state_reference(
                description,
                declared_axis.size,
                context_interface_id=request.interface_id,
                context_component_path=request.component_path,
            )
            observed = (
                _state_value_for_reference(baseline, reference)
                if state_is_usable and baseline is not None and reference is not None
                else None
            )
            observed_size = (
                observed.root
                if observed is not None
                and not isinstance(observed.root, bool)
                and isinstance(observed.root, int)
                and observed.root > 0
                else None
            )
            if observed_size is None:
                plan_problems.append(
                    _collect_axis_state_problem(
                        "instrument_driver_acquisition_axis_state_unknown",
                        f"{command.instrument_id} acquisition result "
                        f"{request.result_id} axis {declared_axis.id} requires "
                        "synchronized size state",
                        reference=reference or declared_axis.size,
                        request_id=request.id,
                        axis_index=axis_index,
                        requested_size=requested_axis.size,
                        observed_value=observed,
                    )
                )
            elif (
                requested_axis.offset is None and requested_axis.size != observed_size
            ) or (
                requested_axis.offset is not None
                and (
                    requested_axis.size is None
                    or requested_axis.offset + requested_axis.size > observed_size
                )
            ):
                plan_problems.append(
                    _collect_axis_state_problem(
                        "instrument_driver_acquisition_axis_state_mismatch",
                        f"{command.instrument_id} acquisition result "
                        f"{request.result_id} axis {declared_axis.id} requires "
                        f"range within size {observed_size} from synchronized "
                        f"state, got offset {requested_axis.offset} and size "
                        f"{requested_axis.size}",
                        reference=reference or declared_axis.size,
                        request_id=request.id,
                        axis_index=axis_index,
                        requested_size=requested_axis.size,
                        observed_value=observed,
                    )
                )
    return plan_problems


def validate_collect_receipt(
    *,
    command: _commands.CollectCommand,
    receipt: _commands.CollectReceipt,
) -> list[Problem]:
    """Validate collection data against the frozen command contract."""

    if receipt.status != "collected":
        return []
    assert receipt.readback is not None
    requests = {request.id: request for request in command.requests}
    values = receipt.readback.values
    problems: list[Problem] = []
    for request_id in sorted(set(requests) - set(values)):
        problems.append(
            _collect_receipt_problem(
                "instrument_driver_missing_acquisition_result",
                f"{command.instrument_id} did not return acquisition request "
                f"{request_id}",
                "readback",
                "values",
                request_id,
            )
        )
    for request_id in sorted(set(values) - set(requests)):
        problems.append(
            _collect_receipt_problem(
                "instrument_driver_unexpected_acquisition_result",
                f"{command.instrument_id} returned unexpected acquisition request "
                f"{request_id}",
                "readback",
                "values",
                request_id,
            )
        )
    for request_id in sorted(set(requests) & set(values)):
        request = requests[request_id]
        value = values[request_id]
        expected_shape = tuple(axis.size for axis in request.dimensions)
        for issue in measurement_value_contract_issues(
            value,
            expected_dtype=request.dtype,
            expected_unit=request.unit,
            expected_shape=expected_shape,
        ):
            problems.append(
                _collect_receipt_problem(
                    f"instrument_driver_readback_{issue.code.value}",
                    f"{command.instrument_id} acquisition request "
                    f"{request_id} violates "
                    f"its requested {issue.code.value} contract",
                    "readback",
                    "values",
                    request_id,
                    *issue.path,
                )
            )
    return problems


def project_instrument_state(
    state: _InstrumentStateSnapshot | _ProjectedInstrumentState,
    command: _commands.InstrumentStateCommand,
    *,
    description: InstrumentDescription,
) -> _ProjectedInstrumentState:
    """Project preflight knowledge without claiming a hardware observation."""

    observations = {
        _state_member_identity(item.target): item.model_copy(deep=True)
        for item in state.observations
    }
    for assignment in command.assignments:
        property_spec = resolve_state_member_spec(description, assignment.target)
        if property_spec is not None and property_spec.access == "write_only":
            continue
        observations[_state_member_identity(assignment.target)] = (
            _InstrumentStateObservation(
                target=assignment.target,
                value=assignment.value,
                source="command_confirmed",
                entity_ids=tuple(assignment.entity_ids),
                channel_bindings=tuple(assignment.channel_bindings),
            )
        )
    return _ProjectedInstrumentState(
        instrument_id=state.instrument_id,
        observations=tuple(
            observations[key]
            for key in sorted(observations, key=_state_member_sort_key)
        ),
    )


def project_instrument_invoke_state(
    state: _InstrumentStateSnapshot | _ProjectedInstrumentState,
    command: _commands.InvokeCommand,
    *,
    description: InstrumentDescription,
) -> _ProjectedInstrumentState:
    """Project the state knowledge retained after one declared operation."""

    interface_spec = next(
        item for item in description.interfaces if item.id == command.interface_id
    )
    component_spec = resolve_implementation_component(
        description,
        interface_spec,
        command.component_path,
    )
    assert component_spec is not None
    operation_spec = next(
        item for item in component_spec.operations if item.id == command.operation_id
    )
    resolved_invalidations = tuple(
        resolve_implementation_state_reference(
            description,
            declared,
            context_interface_id=command.interface_id,
            context_component_path=command.component_path,
        )
        for declared in operation_spec.invalidates
    )
    assert all(reference is not None for reference in resolved_invalidations)
    invalidated = {
        _state_member_identity(
            PropertyRef(
                reference.interface_id,
                tuple(reference.component_path),
                reference.property_id,
            )
        )
        for reference in resolved_invalidations
        if reference is not None
    }
    observations = {
        _state_member_identity(item.target): item.model_copy(deep=True)
        for item in state.observations
        if _state_member_identity(item.target) not in invalidated
    }
    return _ProjectedInstrumentState(
        instrument_id=state.instrument_id,
        observations=tuple(
            observations[key]
            for key in sorted(observations, key=_state_member_sort_key)
        ),
    )


def operation_invalidated_state_members(
    description: InstrumentDescription,
    *,
    interface_id: InterfaceId,
    component_path: Sequence[str],
    operation_id: str,
) -> tuple[PropertyRef, ...]:
    """Resolve one operation's invalidation declarations to physical targets."""

    interface_spec = next(
        item for item in description.interfaces if item.id == interface_id
    )
    component_spec = resolve_implementation_component(
        description,
        interface_spec,
        component_path,
    )
    assert component_spec is not None
    operation_spec = next(
        item for item in component_spec.operations if item.id == operation_id
    )
    resolved = tuple(
        resolve_implementation_state_reference(
            description,
            declared,
            context_interface_id=interface_id,
            context_component_path=component_path,
        )
        for declared in operation_spec.invalidates
    )
    assert all(reference is not None for reference in resolved)
    return tuple(
        PropertyRef(
            reference.interface_id,
            tuple(reference.component_path),
            reference.property_id,
        )
        for reference in resolved
        if reference is not None
    )


def capture_state_members(
    description: InstrumentDescription,
) -> tuple[StateMemberRef, ...]:
    """Return the exact members required at lifecycle capture boundaries."""

    members: list[StateMemberRef] = []

    def walk(
        interface_id: str,
        component_path: tuple[str, ...],
        component_spec: InterfaceSpec | ComponentSpec,
    ) -> None:
        members.extend(
            PropertyRef(interface_id, component_path, property_spec.id)
            for property_spec in component_spec.properties
            if (
                effective := resolve_state_member_spec(
                    description,
                    _state_member_target(
                        PropertyRef(interface_id, component_path, property_spec.id)
                    ),
                )
            )
            is not None
            and effective.capture
        )
        for child in component_spec.components:
            walk(
                interface_id,
                (*component_path, child.id),
                child,
            )

    mounts_by_interface = _interface_mounts_by_id(description)
    for interface_spec in description.interfaces:
        mounts = mounts_by_interface.get(interface_spec.id)
        if mounts is None:
            walk(interface_spec.id, (), interface_spec)
            continue
        for mount in mounts:
            walk(interface_spec.id, mount, interface_spec)
    for device_schema in description.device_schemas:
        members.extend(
            DevicePropertyRef(
                device_schema.id,
                tuple(member.component_path),
                member.property.id,
            )
            for member in device_schema.members
            if member.property.capture
        )
    return tuple(sorted(members, key=repr))


def state_capture_request(
    description: InstrumentDescription,
) -> DriverStateReadRequest:
    """Build the driver request for one lifecycle capture plan."""

    return DriverStateReadRequest(frozenset(capture_state_members(description)))


def restorable_state_members(
    description: InstrumentDescription,
) -> frozenset[StateMemberRef]:
    """Return members explicitly declared safe for baseline restoration."""

    return frozenset(
        target
        for target in capture_state_members(description)
        if (
            spec := resolve_state_member_spec(
                description,
                _state_member_target(target),
            )
        )
        is not None
        and spec.restore
    )


def resolve_state_member_spec(
    description: InstrumentDescription,
    target: _StateMemberTarget,
) -> PropertySpec | None:
    if isinstance(target, _InterfaceStateMemberTarget):
        reference = StatePropertyRef(
            interface_id=target.interface_id,
            component_path=list(target.component_path),
            property_id=target.property_id,
        )
        declared = _resolve_interface_property_spec(description, reference)
        if declared is None:
            return None
        implementation = next(
            (
                item
                for item in description.interface_property_implementations
                if item.property == reference
            ),
            None,
        )
        if implementation is None:
            return declared
        updates: dict[str, object] = {
            "access": implementation.access,
            "capture": implementation.capture,
            "restore": implementation.restore,
        }
        if implementation.value_type is not None:
            updates["value_type"] = implementation.value_type
        return declared.model_copy(update=updates)
    device_schema = next(
        (
            schema
            for schema in description.device_schemas
            if schema.id == target.schema_id
        ),
        None,
    )
    if device_schema is None:
        return None
    return next(
        (
            member.property
            for member in device_schema.members
            if tuple(member.component_path) == target.component_path
            and member.property.id == target.property_id
        ),
        None,
    )


def _resolve_interface_property_spec(
    description: InstrumentDescription,
    reference: StatePropertyRef,
) -> PropertySpec | None:
    interface_spec = next(
        (
            interface
            for interface in description.interfaces
            if interface.id == reference.interface_id
        ),
        None,
    )
    if interface_spec is None:
        return None
    component_spec = resolve_implementation_component(
        description,
        interface_spec,
        reference.component_path,
    )
    if component_spec is None:
        return None
    return next(
        (
            property_spec
            for property_spec in component_spec.properties
            if property_spec.id == reference.property_id
        ),
        None,
    )


def _property_access_permissions(access: PropertyAccess) -> frozenset[str]:
    if access == "read_only":
        return frozenset({"read"})
    if access == "write_only":
        return frozenset({"write"})
    return frozenset({"read", "write"})


def _validate_state_value(
    *,
    property_id: str,
    value: StateValue,
    spec: PropertySpec,
    assignment_path: tuple[LocationPathItem, ...],
) -> list[Problem]:
    try:
        validate_literal(spec.value_type, value.root, path=("value",))
    except ValueValidationError as error:
        return _property_value_mismatch(
            property_id,
            error.reason,
            path=(*assignment_path, *error.path),
        )
    return []


def _validate_operation_argument(
    *,
    argument_id: str,
    value: StateValue,
    spec: OperationArgumentSpec,
    payload_schemas: Mapping[str, str],
) -> list[Problem]:
    atom = spec.value_type.atom
    literal = value.root
    if isinstance(atom, PayloadType):
        if not isinstance(literal, PayloadRef):
            return _operation_argument_value_mismatch(
                argument_id,
                f"expected payload reference, got {literal!r}",
            )
        schema_id = payload_schemas.get(literal.payload_id)
        if schema_id is None:
            return [
                _invoke_problem(
                    "instrument_driver_unknown_payload",
                    f"{argument_id} references unknown payload {literal.payload_id}",
                    "arguments",
                    argument_id,
                    "value",
                    "payload_id",
                )
            ]
        if schema_id != atom.schema_id:
            return _operation_argument_value_mismatch(
                argument_id,
                f"expected payload {atom.schema_id!r}, got {schema_id!r}",
                path=("value", "payload_id"),
            )
        return []
    try:
        validate_literal(spec.value_type, literal, path=("value",))
    except ValueValidationError as error:
        return _operation_argument_value_mismatch(
            argument_id,
            error.reason,
            path=error.path,
        )
    return []


def _operation_argument_value_mismatch(
    argument_id: str,
    reason: str,
    *,
    path: ValuePath = ("value",),
) -> list[Problem]:
    return [
        _invoke_problem(
            "instrument_driver_operation_argument_value_mismatch",
            f"{argument_id}: {reason}",
            "arguments",
            argument_id,
            *path,
        )
    ]


def _property_value_mismatch(
    property_id: str,
    reason: str,
    *,
    path: ValuePath = ("value",),
) -> list[Problem]:
    return [
        _problem(
            "instrument_driver_property_value_mismatch",
            f"{property_id}: {reason}",
            *path,
        )
    ]


def _validate_instrument_scalar(
    value: Scalar,
    *,
    allow_payload: bool,
) -> Scalar:
    if not isinstance(
        value.atom,
        BoolType | IntType | FloatType | StringType | QuantityType | PayloadType,
    ):
        raise ValueError(
            "instrument values support bool, int, float, string, quantity, and payload"
        )
    if isinstance(value.atom, PayloadType) and not allow_payload:
        raise ValueError("instrument properties cannot contain payload values")
    if isinstance(value.atom, IntType):
        if (
            value.atom.minimum is not None and value.atom.minimum < -_JSON_SAFE_INTEGER
        ) or (
            value.atom.maximum is not None and value.atom.maximum > _JSON_SAFE_INTEGER
        ):
            raise ValueError(
                "instrument integers must stay within the JSON safe integer range"
            )
        return Scalar(
            IntType(
                minimum=(
                    -_JSON_SAFE_INTEGER
                    if value.atom.minimum is None
                    else value.atom.minimum
                ),
                maximum=(
                    _JSON_SAFE_INTEGER
                    if value.atom.maximum is None
                    else value.atom.maximum
                ),
            )
        )
    if isinstance(value.atom, FloatType | QuantityType) and not value.atom.finite:
        raise ValueError("instrument numeric values must require finite values")
    if isinstance(value.atom, FloatType):
        return Scalar(
            FloatType(
                minimum=_canonical_float(value.atom.minimum),
                maximum=_canonical_float(value.atom.maximum),
            )
        )
    if isinstance(value.atom, QuantityType):
        return Scalar(
            QuantityType(
                dimension=value.atom.dimension,
                unit=value.atom.unit,
                minimum=_canonical_float(value.atom.minimum),
                maximum=_canonical_float(value.atom.maximum),
            )
        )
    return value


def _canonical_float(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        selected = float(value)
    except OverflowError as error:
        raise ValueError("instrument numeric bounds must be finite") from error
    return 0.0 if selected == 0.0 else selected


def _require_unique(values: Iterable[str], label: str) -> None:
    selected = tuple(values)
    if len(selected) != len(set(selected)):
        raise ValueError(f"{label} must be unique")


def validate_instrument_description_collection(
    instruments: Sequence[InstrumentDescription],
) -> None:
    """Require stable identities across one advertised instrument collection."""

    instrument_ids = [instrument.instrument_id for instrument in instruments]
    duplicates = sorted(
        instrument_id
        for instrument_id in set(instrument_ids)
        if instrument_ids.count(instrument_id) > 1
    )
    if duplicates:
        raise ValueError(
            "instrument descriptions require unique instrument ids: "
            f"{', '.join(duplicates)}"
        )
    declared: dict[str, str] = {}
    for instrument in instruments:
        _validate_contract_state_references(instrument)
        for interface_spec in instrument.interfaces:
            fingerprint = model_wire_content_hash(interface_spec)
            previous = declared.setdefault(interface_spec.id, fingerprint)
            if previous != fingerprint:
                raise ValueError(
                    f"interface {interface_spec.id!r} must have one stable "
                    "specification within an instrument catalog"
                )


def _validate_contract_state_references(
    description: InstrumentDescription,
) -> None:
    for interface_spec in description.interfaces:
        for component_spec in _component_specs(interface_spec):
            for operation_spec in component_spec.operations:
                for reference in operation_spec.invalidates:
                    if _resolve_state_property(description, reference) is None:
                        raise ValueError(
                            f"operation {operation_spec.id!r} invalidation must "
                            "reference a declared property"
                        )
            for acquisition_spec in component_spec.acquisitions:
                for precondition in acquisition_spec.preconditions:
                    _validate_acquisition_precondition(
                        description,
                        acquisition_spec,
                        precondition,
                    )
                for result in acquisition_spec.results:
                    _validate_acquisition_result_axis_state(
                        description,
                        acquisition_spec,
                        result,
                    )


def _validate_acquisition_precondition(
    description: InstrumentDescription,
    acquisition: AcquisitionSpec,
    precondition: AcquisitionPreconditionSpec,
) -> None:
    resolved = _resolve_state_property(description, precondition.property)
    if resolved is None:
        raise ValueError(
            f"acquisition {acquisition.id!r} precondition must reference "
            "a declared property"
        )
    _, property_spec = resolved
    if property_spec.access == "write_only":
        raise ValueError(
            f"acquisition {acquisition.id!r} precondition property must be observable"
        )
    atom = property_spec.value_type.atom
    if isinstance(atom, QuantityType):
        literal = precondition.value
        if not isinstance(literal, Quantity):
            raise ValueError(
                f"acquisition {acquisition.id!r} quantity precondition requires "
                "an explicit unit"
            )
        if atom.unit is None:
            raise ValueError(
                f"acquisition {acquisition.id!r} quantity precondition property "
                "must declare a canonical unit"
            )
        if literal.unit != atom.unit:
            raise ValueError(
                f"acquisition {acquisition.id!r} precondition must use canonical "
                f"unit {atom.unit!r}"
            )
    try:
        canonical_value = coerce_literal(
            property_spec.value_type,
            precondition.value,
            path=("value",),
        )
    except ValueValidationError as error:
        raise ValueError(
            f"acquisition {acquisition.id!r} precondition value: {error.reason}"
        ) from error
    if isinstance(canonical_value, float) and canonical_value == 0.0:
        canonical_value = 0.0
    elif isinstance(canonical_value, Quantity) and canonical_value.value == 0.0:
        canonical_value = canonical_value.model_copy(update={"value": 0.0})
    precondition.value = cast(
        "bool | int | float | str | Quantity",
        canonical_value,
    )


def _validate_acquisition_result_axis_state(
    description: InstrumentDescription,
    acquisition: AcquisitionSpec,
    result: AcquisitionResultSpec,
) -> None:
    for axis in result.axes:
        if not isinstance(axis.size, StatePropertyRef):
            continue
        reference = axis.size
        resolved = _resolve_state_property(description, reference)
        if resolved is None:
            raise ValueError(
                f"acquisition {acquisition.id!r} result {result.id!r} axis "
                f"{axis.id!r} size must reference a declared property"
            )
        _, property_spec = resolved
        if property_spec.access == "write_only":
            raise ValueError(
                f"acquisition {acquisition.id!r} result {result.id!r} axis "
                f"{axis.id!r} size property must be observable"
            )
        atom = property_spec.value_type.atom
        if not isinstance(atom, IntType):
            raise ValueError(
                f"acquisition {acquisition.id!r} result {result.id!r} axis "
                f"{axis.id!r} size property must be an integer"
            )
        if atom.minimum is None or atom.minimum < 1:
            raise ValueError(
                f"acquisition {acquisition.id!r} result {result.id!r} axis "
                f"{axis.id!r} size property must have a positive minimum"
            )


def _resolve_state_property(
    description: InstrumentDescription,
    reference: StatePropertyRef,
) -> tuple[InterfaceSpec | ComponentSpec, PropertySpec] | None:
    interface = next(
        (
            candidate
            for candidate in description.interfaces
            if candidate.id == reference.interface_id
        ),
        None,
    )
    if interface is None:
        return None
    component = _resolve_component(interface, reference.component_path)
    if component is None:
        return None
    property_spec = next(
        (
            candidate
            for candidate in component.properties
            if candidate.id == reference.property_id
        ),
        None,
    )
    return (
        None
        if property_spec is None
        else (
            component,
            property_spec,
        )
    )


def _component_specs(
    spec: InterfaceSpec | ComponentSpec,
) -> Iterable[InterfaceSpec | ComponentSpec]:
    yield spec
    for child in spec.components:
        yield from _component_specs(child)


def _validate_component_members(
    spec: InterfaceSpec | ComponentSpec,
) -> None:
    _require_unique((item.id for item in spec.properties), "property ids")
    _require_unique((item.id for item in spec.operations), "operation ids")
    _require_unique((item.id for item in spec.acquisitions), "acquisition ids")
    _require_unique((item.id for item in spec.components), "component ids")


def _resolve_component(
    interface_spec: InterfaceSpec,
    component_path: Sequence[str],
) -> InterfaceSpec | ComponentSpec | None:
    selected: InterfaceSpec | ComponentSpec = interface_spec
    for component_id in component_path:
        match = next(
            (
                component
                for component in selected.components
                if component.id == component_id
            ),
            None,
        )
        if match is None:
            return None
        selected = match
    return selected


def resolve_implementation_component(
    description: InstrumentDescription,
    interface_spec: InterfaceSpec,
    component_path: Sequence[str],
) -> InterfaceSpec | ComponentSpec | None:
    """Resolve a physical target through declared mounts into its contract."""

    resolved = _resolve_implementation_target(
        description,
        interface_spec,
        component_path,
    )
    return None if resolved is None else resolved[0]


def resolve_implementation_state_reference(
    description: InstrumentDescription,
    reference: StatePropertyRef,
    *,
    context_interface_id: InterfaceId,
    context_component_path: Sequence[str],
) -> StatePropertyRef | None:
    """Mount one contract-relative state reference beside a physical target."""

    context_interface = next(
        (
            interface_spec
            for interface_spec in description.interfaces
            if interface_spec.id == context_interface_id
        ),
        None,
    )
    if context_interface is None:
        return None
    context_target = _resolve_implementation_target(
        description,
        context_interface,
        context_component_path,
    )
    if context_target is None:
        return None
    _component_spec, context_mount = context_target
    reference_mounts = _interface_mounts_by_id(description).get(reference.interface_id)
    if reference_mounts is None:
        physical_path = tuple(reference.component_path)
    elif context_mount in reference_mounts:
        physical_path = (*context_mount, *reference.component_path)
    else:
        return None
    return reference.model_copy(update={"component_path": list(physical_path)})


def _physical_state_reference(
    description: InstrumentDescription,
    reference: StatePropertyRef,
    *,
    context_interface_id: InterfaceId | None,
    context_component_path: Sequence[str],
) -> StatePropertyRef | None:
    if context_interface_id is None:
        return reference
    return resolve_implementation_state_reference(
        description,
        reference,
        context_interface_id=context_interface_id,
        context_component_path=context_component_path,
    )


def _resolve_implementation_target(
    description: InstrumentDescription,
    interface_spec: InterfaceSpec,
    component_path: Sequence[str],
) -> tuple[InterfaceSpec | ComponentSpec, tuple[str, ...]] | None:
    mounts = _interface_mounts_by_id(description).get(interface_spec.id)
    selected_path = tuple(component_path)
    if mounts is None:
        component_spec = _resolve_component(interface_spec, selected_path)
        return None if component_spec is None else (component_spec, ())
    for mount in sorted(mounts, key=len, reverse=True):
        if not _path_prefix(mount, selected_path):
            continue
        component_spec = _resolve_component(
            interface_spec,
            selected_path[len(mount) :],
        )
        if component_spec is not None:
            return component_spec, mount
    return None


def _resolve_instrument_component(
    description: InstrumentDescription,
    component_path: Sequence[str],
) -> InstrumentDescription | InstrumentComponentSpec | None:
    selected: InstrumentDescription | InstrumentComponentSpec = description
    for component_id in component_path:
        match = next(
            (
                component
                for component in selected.components
                if component.id == component_id
            ),
            None,
        )
        if match is None:
            return None
        selected = match
    return selected


def _path_prefix(prefix: Sequence[str], path: Sequence[str]) -> bool:
    return tuple(path[: len(prefix)]) == tuple(prefix)


def _interface_mounts_by_id(
    description: InstrumentDescription,
) -> dict[InterfaceId, tuple[tuple[str, ...], ...]]:
    selected: dict[InterfaceId, list[tuple[str, ...]]] = {}
    for mount in description.interface_mounts:
        selected.setdefault(mount.interface_id, []).append(tuple(mount.component_path))
    return {interface_id: tuple(paths) for interface_id, paths in selected.items()}


def _problem(code: str, message: str, *path: LocationPathItem) -> Problem:
    return problem(
        code,
        message,
        phase=ProblemPhase.EXECUTION,
        location=model_location("instrument_state_command", *path),
    )


def _invoke_problem(
    code: str,
    message: str,
    *path: LocationPathItem,
) -> Problem:
    return problem(
        code,
        message,
        phase=ProblemPhase.EXECUTION,
        location=model_location("instrument_invoke_command", *path),
    )


def _interactive_collect_problem(
    code: str,
    message: str,
    *path: LocationPathItem,
    related_locations: Sequence[ModelLocation] = (),
    details: Mapping[str, object] | None = None,
) -> Problem:
    return problem(
        code,
        message,
        phase=ProblemPhase.EXECUTION,
        location=model_location("interactive_collect_intent", *path),
        related_locations=related_locations,
        details=details,
    )


def _interactive_collect_precondition_problem(
    code: str,
    message: str,
    *,
    issue: AcquisitionReadinessIssue,
) -> Problem:
    precondition = issue.precondition
    details: dict[str, object] = {
        "precondition": precondition.model_dump(mode="json"),
    }
    if issue.observed_value is not None:
        details["observed_value"] = issue.observed_value.model_dump(mode="json")
    target = issue.resolved_property or precondition.property
    if issue.resolved_property is not None:
        details["resolved_property"] = issue.resolved_property.model_dump(mode="json")
    return _interactive_collect_problem(
        code,
        message,
        "acquisition_id",
        related_locations=(
            _state_property_location(
                target.interface_id,
                target.component_path,
                target.property_id,
            ),
        ),
        details=details,
    )


def _interactive_collect_axis_state_problem(
    *,
    intent: _commands.InteractiveCollectIntent,
    result_id: str,
    result_index: int | None,
    axis: AcquisitionAxisSpec,
    reference: StatePropertyRef,
    axis_index: int,
    observed_value: StateValue | None,
) -> Problem:
    details: dict[str, object] = {
        "result_id": result_id,
        "axis_id": axis.id,
        "axis_index": axis_index,
        "state_property": reference.model_dump(mode="json"),
    }
    if observed_value is not None:
        details["observed_value"] = observed_value.model_dump(mode="json")
    path: tuple[LocationPathItem, ...] = (
        ("acquisition_id",) if result_index is None else ("result_ids", result_index)
    )
    return _interactive_collect_problem(
        "instrument_driver_acquisition_axis_state_unknown",
        f"{intent.instrument_id} acquisition result {result_id} axis "
        f"{axis.id} requires synchronized size state",
        *path,
        related_locations=(
            _state_property_location(
                reference.interface_id,
                reference.component_path,
                reference.property_id,
            ),
        ),
        details=details,
    )


def _collect_command_problem(
    code: str,
    message: str,
    *path: LocationPathItem,
) -> Problem:
    return problem(
        code,
        message,
        phase=ProblemPhase.EXECUTION,
        location=model_location("instrument_collect_command", *path),
    )


def _collect_precondition_problem(
    code: str,
    message: str,
    *,
    issue: AcquisitionReadinessIssue,
    request_id: str,
) -> Problem:
    precondition = issue.precondition
    details: dict[str, object] = {
        "precondition": precondition.model_dump(mode="json"),
    }
    if issue.observed_value is not None:
        details["observed_value"] = issue.observed_value.model_dump(mode="json")
    target = issue.resolved_property or precondition.property
    if issue.resolved_property is not None:
        details["resolved_property"] = issue.resolved_property.model_dump(mode="json")
    return problem(
        code,
        message,
        phase=ProblemPhase.EXECUTION,
        location=model_location(
            "instrument_collect_command",
            "requests",
            request_id,
            "acquisition_id",
        ),
        related_locations=(
            _state_property_location(
                target.interface_id,
                target.component_path,
                target.property_id,
            ),
        ),
        details=details,
    )


def _collect_axis_state_problem(
    code: str,
    message: str,
    *,
    reference: StatePropertyRef,
    request_id: str,
    axis_index: int,
    requested_size: int | None,
    observed_value: StateValue | None,
) -> Problem:
    details: dict[str, object] = {
        "state_property": reference.model_dump(mode="json"),
        "requested_size": requested_size,
    }
    if observed_value is not None:
        details["observed_value"] = observed_value.model_dump(mode="json")
    return problem(
        code,
        message,
        phase=ProblemPhase.EXECUTION,
        location=model_location(
            "instrument_collect_command",
            "requests",
            request_id,
            "dimensions",
            axis_index,
            "size",
        ),
        related_locations=(
            _state_property_location(
                reference.interface_id,
                reference.component_path,
                reference.property_id,
            ),
        ),
        details=details,
    )


def _collect_receipt_problem(
    code: str,
    message: str,
    *path: LocationPathItem,
) -> Problem:
    return problem(
        code,
        message,
        phase=ProblemPhase.EXECUTION,
        location=model_location("instrument_collect_receipt", *path),
    )


def _snapshot_problem(
    code: str,
    message: str,
    *path: LocationPathItem,
    related_locations: Sequence[ModelLocation] = (),
    details: Mapping[str, object] | None = None,
) -> Problem:
    return problem(
        code,
        message,
        phase=ProblemPhase.EXECUTION,
        location=model_location("instrument_state_snapshot", *path),
        related_locations=related_locations,
        details=details,
    )


def _state_property_location(
    interface_id: str,
    component_path: Sequence[str],
    property_id: str,
) -> ModelLocation:
    return model_location(
        "instrument_state",
        interface_id,
        *component_path,
        property_id,
    )

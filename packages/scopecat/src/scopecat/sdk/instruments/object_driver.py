"""Glue from explicit Python declarations to the driver protocol."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from inspect import Parameter, signature
from typing import Literal, cast
from uuid import uuid4

from pydantic import JsonValue

from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_type_compatibility import is_assignable
from scopecat.kernel.value_types import Float as FloatType
from scopecat.kernel.value_types import Int as IntType
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.kernel.value_types import Scalar
from scopecat.kernel.value_types import String as StringType
from scopecat.records.instrument import ObservationSource
from scopecat.records.measurement import (
    MeasurementAcquisitionValue,
    MeasurementScalar,
)
from scopecat.sdk.instruments.authoring import (
    DriverAcquisition,
    DriverAcquisitionPlan,
    DriverOperation,
    DriverOutcome,
    DriverReadback,
    DriverRejected,
    DriverScalar,
    DriverStateObservation,
    DriverStatePatch,
    DriverStateReadback,
    DriverStateReadRequest,
    DriverSuccess,
    DriverUnknown,
)
from scopecat.sdk.instruments.contracts import (
    DeviceStateMemberSpec,
    DeviceStateSpec,
    InstrumentDescription,
    InterfacePropertyImplementationSpec,
    StatePropertyRef,
)
from scopecat.sdk.instruments.declarations import (
    DeclaredAcquisition,
    DeclaredDeviceProperty,
    DeclaredInterfaceLayout,
    DeclaredOperation,
    DeclaredProperty,
    DeviceMember,
    Member,
    compile_interface,
    declared_device_properties,
    declared_interface_layout,
)
from scopecat.sdk.instruments.members import (
    AcquisitionRef,
    AcquisitionResultRef,
    DevicePropertyRef,
    DeviceSchemaId,
    OperationRef,
    PropertyRef,
    StateMemberRef,
)
from scopecat.sdk.problems import ProblemPhase, model_location, problem

_DRIVER_METADATA = "__scopecat_instrument_driver__"
_DRIVER_READ_METADATA = "__scopecat_instrument_read__"
_DRIVER_WRITE_METADATA = "__scopecat_instrument_write__"
_DRIVER_IMPLEMENTATION_METADATA = "__scopecat_instrument_implementation__"

type DriverMember = Member[object] | DeviceMember[object]


@dataclass(frozen=True, slots=True)
class DriverMemberBinding:
    """One method binding that may cover one member or a coherent group."""

    members: tuple[DriverMember, ...]


@dataclass(frozen=True, slots=True)
class DriverImplementationBinding:
    """One driver method bound to an interface operation or acquisition."""

    declaration: Callable[..., object]


@dataclass(frozen=True, slots=True)
class DriverMemberPolicy:
    """Exception-only lifecycle narrowing for one portable member."""

    member: Member[object]
    capture: Literal[False] | None = None
    restore: Literal[False] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.member, DeviceMember):
            raise TypeError("device member lifecycle belongs in device_member(...)")
        if (self.capture is not None and self.capture is not False) or (
            self.restore is not None and self.restore is not False
        ):
            raise ValueError("driver member policy can only disable lifecycle behavior")
        if self.capture is None and self.restore is None:
            raise ValueError("driver member policy must disable capture or restoration")
        if self.capture is False and self.restore is False:
            raise ValueError(
                "disabling capture already disables restoration for the member"
            )


def member_policy(
    member: Member[object],
    /,
    *,
    capture: Literal[False] | None = None,
    restore: Literal[False] | None = None,
) -> DriverMemberPolicy:
    """Narrow capture or restoration for one concrete driver implementation."""

    return DriverMemberPolicy(member, capture=capture, restore=restore)


@dataclass(frozen=True, slots=True)
class DriverMemberConstraint:
    """Concrete value-set narrowing for one portable member."""

    member: Member[object]
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.member, DeviceMember):
            raise TypeError("device member constraints belong in device_member(...)")
        if self.minimum is None and self.maximum is None and self.choices is None:
            raise ValueError("driver member constraint must narrow admitted values")
        if self.choices is not None and (
            self.minimum is not None or self.maximum is not None
        ):
            raise ValueError("driver member constraint cannot mix bounds and choices")
        if self.choices is not None and (
            not self.choices or len(set(self.choices)) != len(self.choices)
        ):
            raise ValueError(
                "driver member constraint choices must be non-empty and unique"
            )


def member_constraint(
    member: Member[object],
    /,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    choices: Sequence[str] | None = None,
) -> DriverMemberConstraint:
    """Narrow the values admitted by one concrete driver implementation."""

    return DriverMemberConstraint(
        member,
        minimum=minimum,
        maximum=maximum,
        choices=None if choices is None else tuple(choices),
    )


class _NoChange:
    __slots__ = ()


_NO_CHANGE = _NoChange()


@dataclass(frozen=True, slots=True)
class Change[ValueT]:
    """One optional member assignment supplied to a grouped update method."""

    _value: ValueT | _NoChange = _NO_CHANGE

    @property
    def requested(self) -> bool:
        return self._value is not _NO_CHANGE

    @property
    def value(self) -> ValueT:
        if isinstance(self._value, _NoChange):
            raise ValueError("unchanged member has no requested value")
        return self._value


@dataclass(frozen=True, slots=True)
class Observed[ValueT]:
    """One driver value with explicit observation provenance."""

    value: ValueT
    source: ObservationSource = "hardware_query"
    evidence: dict[str, JsonValue] = dataclass_field(default_factory=dict)


def observed[ValueT](
    value: ValueT,
    /,
    *,
    source: ObservationSource = "hardware_query",
    evidence: Mapping[str, JsonValue] | None = None,
) -> Observed[ValueT]:
    """Attach source and evidence to one value returned by a driver reader."""

    return Observed(
        value,
        source=source,
        evidence={} if evidence is None else dict(evidence),
    )


def read[MethodT: Callable[..., object]](
    member: DriverMember,
    /,
) -> Callable[[MethodT], MethodT]:
    """Bind one no-argument driver method as a member reader."""

    return _bind_driver_members(_DRIVER_READ_METADATA, (member,))


def query[MethodT: Callable[..., object]](
    *members: DriverMember,
) -> Callable[[MethodT], MethodT]:
    """Bind one method that returns a tuple for a coherent member group."""

    if len(members) < 2:
        raise ValueError("@query requires at least two members; use @read for one")
    return _bind_driver_members(_DRIVER_READ_METADATA, members)


def write[MethodT: Callable[..., object]](
    member: DriverMember,
    /,
) -> Callable[[MethodT], MethodT]:
    """Bind one single-value driver method as a member writer."""

    return _bind_driver_members(_DRIVER_WRITE_METADATA, (member,))


def update[MethodT: Callable[..., object]](
    *members: DriverMember,
) -> Callable[[MethodT], MethodT]:
    """Bind a grouped update receiving one ``Change`` keyword per member."""

    if len(members) < 2:
        raise ValueError("@update requires at least two members; use @write for one")
    return _bind_driver_members(_DRIVER_WRITE_METADATA, members)


def implements[MethodT: Callable[..., object]](
    declaration: Callable[..., object],
    /,
) -> Callable[[MethodT], MethodT]:
    """Bind one driver method to a declared operation or true acquisition."""

    binding = DriverImplementationBinding(declaration)

    def decorate(method: MethodT) -> MethodT:
        if getattr(method, _DRIVER_IMPLEMENTATION_METADATA, None) is not None:
            raise TypeError("a driver method cannot repeat its implementation binding")
        setattr(method, _DRIVER_IMPLEMENTATION_METADATA, binding)
        return method

    return decorate


def _bind_driver_members[MethodT: Callable[..., object]](
    attribute: str,
    members: Sequence[DriverMember],
) -> Callable[[MethodT], MethodT]:
    if len({id(member) for member in members}) != len(members):
        raise ValueError("a driver I/O binding cannot repeat a member")
    declaration = DriverMemberBinding(tuple(members))

    def decorate(method: MethodT) -> MethodT:
        if getattr(method, attribute, None) is not None:
            raise TypeError("a driver method cannot repeat the same I/O binding kind")
        setattr(method, attribute, declaration)
        return method

    return decorate


@dataclass(frozen=True, slots=True)
class InstrumentDriverMetadata:
    implementation_id: str
    implementation_version: str
    interfaces: tuple[type[object], ...]
    member_policies: tuple[DriverMemberPolicy, ...]
    member_constraints: tuple[DriverMemberConstraint, ...]
    label: str | None
    description: str | None
    device_schema_id: DeviceSchemaId | None
    device_label: str | None
    device_description: str | None


def instrument_driver[DriverT: type[object]](
    implementation_id: str,
    implementation_version: str,
    *,
    interfaces: Sequence[type[object]],
    member_policies: Sequence[DriverMemberPolicy] = (),
    member_constraints: Sequence[DriverMemberConstraint] = (),
    label: str | None = None,
    description: str | None = None,
    device_schema_id: DeviceSchemaId | None = None,
    device_label: str | None = None,
    device_description: str | None = None,
) -> Callable[[DriverT], DriverT]:
    """Bind an ordinary Python implementation class to declared interfaces."""

    metadata = InstrumentDriverMetadata(
        implementation_id=implementation_id,
        implementation_version=implementation_version,
        interfaces=tuple(interfaces),
        member_policies=tuple(member_policies),
        member_constraints=tuple(member_constraints),
        label=label,
        description=description,
        device_schema_id=device_schema_id,
        device_label=device_label,
        device_description=device_description,
    )

    def decorate(cls: DriverT) -> DriverT:
        setattr(cls, _DRIVER_METADATA, metadata)
        setattr(cls, "implementation_id", implementation_id)  # noqa: B010
        setattr(cls, "implementation_version", implementation_version)  # noqa: B010
        _validate_driver_bindings(cls)
        return cls

    return decorate


class ObjectInstrumentDriver:
    """Generic protocol adapter for explicitly bound driver methods.

    ``@read``/``@write`` bind independent I/O. ``@query``/``@update`` expose
    coherent hardware transactions without turning them into aggregate state.
    Protocol methods remain overridable for unusual routing or failure models.
    """

    instrument_id: str  # pyright: ignore[reportUninitializedInstanceVariable]
    implementation_id: str  # pyright: ignore[reportUninitializedInstanceVariable]
    implementation_version: str  # pyright: ignore[reportUninitializedInstanceVariable]

    def declared_interfaces(self) -> tuple[type[object], ...]:
        return _driver_metadata(type(self)).interfaces

    def describe(self) -> InstrumentDescription:
        metadata = _driver_metadata(type(self))
        device_properties = _device_property_bindings(
            type(self),
            metadata.device_schema_id,
        )
        interfaces = [
            compile_interface(interface_type).spec
            for interface_type in self.declared_interfaces()
        ]
        return InstrumentDescription(
            instrument_id=self.instrument_id,
            implementation_id=metadata.implementation_id,
            implementation_version=metadata.implementation_version,
            label=metadata.label,
            description=metadata.description,
            device_schemas=(
                []
                if metadata.device_schema_id is None
                else [
                    DeviceStateSpec(
                        id=metadata.device_schema_id,
                        label=metadata.device_label,
                        description=metadata.device_description,
                        members=[
                            DeviceStateMemberSpec(
                                component_path=list(binding.ref.component_path),
                                property=binding.spec,
                            )
                            for binding in device_properties.values()
                        ],
                    )
                ]
            ),
            interfaces=interfaces,
            interface_property_implementations=(
                _interface_property_implementations(
                    type(self),
                    self.declared_interfaces(),
                )
            ),
        )

    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        observations: list[DriverStateObservation] = []
        for binding in _driver_io_bindings(
            type(self),
            self.declared_interfaces(),
            _DRIVER_READ_METADATA,
        ):
            selected = tuple(
                target for target in binding.targets if target in request.targets
            )
            if not selected:
                continue
            outcome = cast("object", getattr(self, binding.method_name)())
            if len(binding.targets) == 1:
                returned = (outcome,)
            else:
                if not isinstance(outcome, tuple):
                    raise TypeError(
                        f"grouped query {binding.method_name!r} must return "
                        f"{len(binding.targets)} values"
                    )
                returned = cast("tuple[object, ...]", outcome)
                if len(returned) != len(binding.targets):
                    raise TypeError(
                        f"grouped query {binding.method_name!r} must return "
                        f"{len(binding.targets)} values"
                    )
            coherence_id = uuid4().hex if len(binding.targets) > 1 else None
            observed_request = (
                DriverStateReadRequest(frozenset(binding.targets))
                if coherence_id is not None
                else request
            )
            for target, returned_value in zip(
                binding.targets,
                returned,
                strict=True,
            ):
                if target not in observed_request.targets:
                    continue
                item = _as_observed(returned_value)
                observations.append(
                    DriverStateObservation(
                        target=target,
                        value=item.value,
                        source=item.source,
                        coherence_id=coherence_id,
                        metadata=item.evidence,
                    )
                )
        return DriverStateReadback(observations=tuple(observations))

    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        assignments = {assignment.target: assignment for assignment in request.entries}
        handled: set[StateMemberRef] = set()
        metadata: dict[str, JsonValue] = {}
        for binding in _driver_io_bindings(
            type(self),
            self.declared_interfaces(),
            _DRIVER_WRITE_METADATA,
        ):
            selected = assignments.keys() & binding.targets
            if not selected:
                continue
            method = cast("Callable[..., object]", getattr(self, binding.method_name))
            if len(binding.targets) == 1:
                target = binding.targets[0]
                outcome = method(assignments[target].value)
            else:
                outcome = method(
                    **{
                        name: Change(
                            assignments[target].value
                            if target in selected
                            else _NO_CHANGE
                        )
                        for name, target in zip(
                            binding.parameter_names,
                            binding.targets,
                            strict=True,
                        )
                    }
                )
            if isinstance(outcome, (DriverRejected, DriverUnknown)):
                return outcome
            if isinstance(outcome, DriverSuccess):
                metadata.update(outcome.metadata)
            handled.update(selected)
        unsupported = assignments.keys() - handled
        declared_targets = {
            **_interface_property_bindings(self.declared_interfaces()),
            **_device_property_bindings(
                type(self),
                _driver_metadata(type(self)).device_schema_id,
            ),
        }
        unsupported_declared = unsupported & declared_targets.keys()
        if unsupported_declared:
            target = next(iter(unsupported_declared))
            return _unsupported(
                self.instrument_id,
                "state_member",
                target.property_id,
            )
        return DriverSuccess(None, metadata=metadata)

    def invoke(
        self,
        request: DriverOperation,
    ) -> DriverOutcome[DriverStateReadback | None]:
        operation = _operation_bindings(self.declared_interfaces()).get(request.target)
        if operation is None:
            return _unsupported(
                self.instrument_id,
                "operation",
                request.target.operation_id,
            )
        implementation = _implemented_operations(type(self))[request.target]
        outcome = _call_operation(self, implementation, request.arguments)
        if isinstance(outcome, DriverSuccess):
            return DriverSuccess(
                None, metadata=outcome.metadata, measured_cost=outcome.measured_cost
            )
        if outcome is None:
            return DriverSuccess(None)
        return cast("DriverOutcome[DriverStateReadback | None]", outcome)

    def prepare_acquisitions(
        self,
        plan: DriverAcquisitionPlan,
    ) -> DriverOutcome[None]:
        """Accept an acquisition plan; implementations override when needed."""

        del plan
        return DriverSuccess(None)

    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        acquisition = _acquisition_bindings(self.declared_interfaces()).get(
            request.target
        )
        if acquisition is None:
            return _unsupported(
                self.instrument_id,
                "acquisition",
                request.target.acquisition_id,
            )
        if acquisition.kind == "member_observation":
            source_targets = frozenset(
                field.source_property
                for field in acquisition.result_fields
                if field.source_property is not None
            )
            state = self.read_state(DriverStateReadRequest(source_targets))
            observations = {
                observation.target: observation for observation in state.observations
            }
            values: dict[AcquisitionResultRef, MeasurementAcquisitionValue] = {}
            evidence: dict[str, JsonValue] = {}
            for field in acquisition.result_fields:
                source = field.source_property
                if source is None:
                    raise AssertionError(
                        "member observation result must reference a source property"
                    )
                observation = observations[source]
                evidence[field.result_id] = {
                    "source": observation.source,
                    "coherence_id": observation.coherence_id,
                }
                if field.ref not in request.results:
                    continue
                value = observation.value
                if isinstance(value, Quantity):
                    if field.spec.unit is None:
                        raise AssertionError(
                            "quantity member observation result requires a unit"
                        )
                    value = value.to(field.spec.unit).value
                values[field.ref] = MeasurementScalar.create(
                    value=value,
                    dtype=field.spec.dtype,
                    unit=field.spec.unit,
                    metadata=observation.metadata,
                )
            return DriverSuccess(
                DriverReadback(
                    values=values,
                    metadata={"state_observations": evidence},
                )
            )
        implementation = _implemented_acquisitions(type(self))[request.target]
        outcome = cast("object", getattr(self, implementation.method_name)())
        outcome_metadata: dict[str, JsonValue] = {}
        if isinstance(outcome, DriverSuccess):
            result = cast("object", outcome.value)
            outcome_metadata = outcome.metadata
        elif isinstance(outcome, (DriverRejected, DriverUnknown)):
            return outcome
        else:
            result = outcome
        evidence = cast("dict[str, JsonValue]", getattr(result, "evidence", {}))
        values = {
            field.ref: cast(
                "MeasurementAcquisitionValue",
                getattr(result, field.python_name),
            )
            for field in acquisition.result_fields
            if field.ref in request.results
        }
        return DriverSuccess(
            DriverReadback(values=values, metadata=evidence),
            metadata=outcome_metadata,
        )

    def disconnect(self) -> None:
        """Release driver resources; resource-free implementations need no hook."""

    def abort(self) -> None:
        """Best-effort interruption; implementations override when supported."""


def _layouts(
    interfaces: Sequence[type[object]],
) -> tuple[DeclaredInterfaceLayout[object], ...]:
    return tuple(
        declared_interface_layout(compile_interface(interface_type))
        for interface_type in interfaces
    )


def _as_observed(value: object) -> Observed[DriverScalar]:
    if isinstance(value, Observed):
        return cast("Observed[DriverScalar]", value)
    return Observed(cast("DriverScalar", value))


def device_member_ref(member: DeviceMember[object], /) -> DevicePropertyRef:
    """Resolve one driver-owned member declaration to its stable identity."""

    owner = member.owner
    if owner is None:
        raise TypeError("device member declaration is not bound to a driver class")
    metadata = _driver_metadata(owner)
    resolved = next(
        (
            field.ref
            for field in declared_device_properties(owner, metadata.device_schema_id)
            if field.declaration is member
        ),
        None,
    )
    if resolved is None:
        raise TypeError("device member is not declared by its owner")
    return resolved


@dataclass(frozen=True, slots=True)
class _ResolvedDriverBinding:
    method_name: str
    targets: tuple[StateMemberRef, ...]
    parameter_names: tuple[str, ...]


def _driver_io_bindings(
    driver_type: type[object],
    interfaces: Sequence[type[object]],
    attribute: str,
) -> tuple[_ResolvedDriverBinding, ...]:
    metadata = _driver_metadata(driver_type)
    active_fields = {
        **_interface_property_bindings(interfaces),
        **_device_property_bindings(driver_type, metadata.device_schema_id),
    }
    declared = {
        id(field.declaration): (field.ref, field)
        for field in (
            *_interface_property_bindings(metadata.interfaces).values(),
            *_device_property_bindings(
                driver_type,
                metadata.device_schema_id,
            ).values(),
        )
    }
    resolved: list[_ResolvedDriverBinding] = []
    claimed: dict[StateMemberRef, str] = {}
    for method_name, method in _driver_methods(driver_type).items():
        binding = getattr(method, attribute, None)
        if not isinstance(binding, DriverMemberBinding):
            continue
        targets: list[StateMemberRef] = []
        parameter_names: list[str] = []
        for member in binding.members:
            item = declared.get(id(member))
            if item is None:
                raise TypeError(
                    f"driver binding {method_name!r} targets a member not declared "
                    "by this driver"
                )
            target, field = item
            previous = claimed.get(target)
            if previous is not None:
                raise TypeError(
                    f"driver methods {previous!r} and {method_name!r} both bind "
                    f"{target.property_id!r}"
                )
            if attribute == _DRIVER_READ_METADATA and field.spec.access == "write_only":
                raise TypeError(
                    f"driver reader {method_name!r} targets write-only member "
                    f"{target.property_id!r}"
                )
            if attribute == _DRIVER_WRITE_METADATA and field.spec.access == "read_only":
                raise TypeError(
                    f"driver writer {method_name!r} targets read-only member "
                    f"{target.property_id!r}"
                )
            claimed[target] = method_name
            targets.append(target)
            parameter_names.append(field.python_name)
        _validate_driver_binding_method(
            attribute,
            method_name,
            method,
            tuple(parameter_names),
        )
        if not all(target in active_fields for target in targets):
            if any(target in active_fields for target in targets):
                raise TypeError(
                    f"driver binding {method_name!r} mixes active and inactive "
                    "interface members"
                )
            continue
        resolved.append(
            _ResolvedDriverBinding(
                method_name=method_name,
                targets=tuple(targets),
                parameter_names=tuple(parameter_names),
            )
        )
    return tuple(resolved)


def _validate_driver_binding_method(
    attribute: str,
    method_name: str,
    method: object,
    parameter_names: tuple[str, ...],
) -> None:
    if not callable(method):
        raise TypeError(f"driver binding {method_name!r} must decorate a method")
    parameters = tuple(signature(method).parameters.values())
    if not parameters or parameters[0].name != "self":
        raise TypeError(f"driver binding {method_name!r} must be an instance method")
    if attribute == _DRIVER_READ_METADATA:
        if len(parameters) != 1:
            raise TypeError(f"driver reader {method_name!r} must accept only self")
        return
    if len(parameter_names) == 1:
        if len(parameters) != 2 or parameters[1].kind not in (
            Parameter.POSITIONAL_ONLY,
            Parameter.POSITIONAL_OR_KEYWORD,
        ):
            raise TypeError(
                f"driver writer {method_name!r} must accept self and one value"
            )
        return
    if len(set(parameter_names)) != len(parameter_names):
        raise TypeError(
            f"grouped update {method_name!r} has duplicate Python member names"
        )
    update_parameters = parameters[1:]
    if tuple(
        parameter.name for parameter in update_parameters
    ) != parameter_names or any(
        parameter.kind is not Parameter.KEYWORD_ONLY for parameter in update_parameters
    ):
        expected = ", ".join(parameter_names)
        raise TypeError(
            f"grouped update {method_name!r} requires keyword-only Change "
            f"parameters in member order: {expected}"
        )


def _validate_driver_bindings(driver_type: type[object]) -> None:
    metadata = _driver_metadata(driver_type)
    _driver_implementation_bindings(driver_type)
    _driver_member_constraints(metadata)
    interface_fields = _interface_property_bindings(metadata.interfaces)
    device_fields = _device_property_bindings(
        driver_type,
        metadata.device_schema_id,
    )
    fields = {**interface_fields, **device_fields}
    readers = {
        target
        for binding in _driver_io_bindings(
            driver_type,
            metadata.interfaces,
            _DRIVER_READ_METADATA,
        )
        for target in binding.targets
    }
    writers = {
        target
        for binding in _driver_io_bindings(
            driver_type,
            metadata.interfaces,
            _DRIVER_WRITE_METADATA,
        )
        for target in binding.targets
    }
    missing_readers = {
        target
        for target, field in fields.items()
        if field.spec.access != "write_only" and target not in readers
    }
    if missing_readers:
        target = next(iter(missing_readers))
        raise TypeError(
            f"driver has no reader for declared member {target.property_id!r}"
        )
    missing_writers = {
        target
        for target, field in fields.items()
        if (
            field.spec.access == "write_only"
            or (target in device_fields and field.spec.access == "read_write")
        )
        and target not in writers
    }
    if missing_writers:
        target = next(iter(missing_writers))
        raise TypeError(
            f"driver has no writer for declared member {target.property_id!r}"
        )
    redundant_restore_policies = {
        target
        for target, policy in _driver_member_policies(metadata).items()
        if policy.restore is False and target not in writers
    }
    if redundant_restore_policies:
        target = next(iter(redundant_restore_policies))
        raise TypeError(
            f"driver member policy for {target.property_id!r} need not disable "
            "restoration because the implementation is already read-only"
        )


def _interface_property_implementations(
    driver_type: type[object],
    interfaces: Sequence[type[object]],
) -> list[InterfacePropertyImplementationSpec]:
    fields = _interface_property_bindings(interfaces)
    metadata = _driver_metadata(driver_type)
    policies = _driver_member_policies(metadata)
    constraints = _driver_member_constraints(metadata)
    readers = {
        target
        for binding in _driver_io_bindings(
            driver_type,
            interfaces,
            _DRIVER_READ_METADATA,
        )
        for target in binding.targets
    }
    writers = {
        target
        for binding in _driver_io_bindings(
            driver_type,
            interfaces,
            _DRIVER_WRITE_METADATA,
        )
        for target in binding.targets
    }
    implementations: list[InterfacePropertyImplementationSpec] = []
    for target, field in fields.items():
        readable = target in readers
        writable = target in writers
        if readable and writable:
            access = "read_write"
        elif readable:
            access = "read_only"
        elif writable:
            access = "write_only"
        else:
            continue
        policy = policies.get(target)
        constraint = constraints.get(target)
        capture = (
            field.spec.capture
            and readable
            and (policy is None or policy.capture is not False)
        )
        restore = (
            field.spec.restore
            and readable
            and writable
            and capture
            and (policy is None or policy.restore is not False)
        )
        if (
            access == field.spec.access
            and capture == field.spec.capture
            and restore == field.spec.restore
            and constraint is None
        ):
            continue
        implementations.append(
            InterfacePropertyImplementationSpec(
                property=StatePropertyRef(
                    interface_id=target.interface_id,
                    component_path=list(target.component_path),
                    property_id=target.property_id,
                ),
                access=access,
                capture=capture,
                restore=restore,
                value_type=(
                    None
                    if constraint is None
                    else _refined_member_value_type(field, constraint)
                ),
            )
        )
    return implementations


def _driver_member_policies(
    metadata: InstrumentDriverMetadata,
) -> dict[PropertyRef, DriverMemberPolicy]:
    declared = {
        id(field.declaration): (target, field)
        for target, field in _interface_property_bindings(metadata.interfaces).items()
    }
    policies: dict[PropertyRef, DriverMemberPolicy] = {}
    for policy in metadata.member_policies:
        resolved = declared.get(id(policy.member))
        if resolved is None:
            raise TypeError(
                "driver member policy targets an undeclared interface member"
            )
        target, field = resolved
        if target in policies:
            raise TypeError(f"driver member policy repeats {field.ref.property_id!r}")
        if policy.capture is False and not field.spec.capture:
            raise TypeError(
                f"driver member policy for {field.ref.property_id!r} cannot disable "
                "capture because the interface already disables it"
            )
        if policy.restore is False and not field.spec.restore:
            raise TypeError(
                f"driver member policy for {field.ref.property_id!r} cannot disable "
                "restoration because the interface already disables it"
            )
        policies[target] = policy
    return policies


def _driver_member_constraints(
    metadata: InstrumentDriverMetadata,
) -> dict[PropertyRef, DriverMemberConstraint]:
    declared = {
        id(field.declaration): (target, field)
        for target, field in _interface_property_bindings(metadata.interfaces).items()
    }
    constraints: dict[PropertyRef, DriverMemberConstraint] = {}
    for constraint in metadata.member_constraints:
        resolved = declared.get(id(constraint.member))
        if resolved is None:
            raise TypeError(
                "driver member constraint targets an undeclared interface member"
            )
        target, field = resolved
        if target in constraints:
            raise TypeError(
                f"driver member constraint repeats {field.ref.property_id!r}"
            )
        _refined_member_value_type(field, constraint)
        constraints[target] = constraint
    return constraints


def _refined_member_value_type(
    field: DeclaredProperty,
    constraint: DriverMemberConstraint,
) -> Scalar:
    declared = field.spec.value_type
    atom = declared.atom
    if constraint.choices is not None:
        if not isinstance(atom, StringType):
            raise TypeError(
                f"driver member constraint for {field.ref.property_id!r} cannot "
                "apply choices to a non-string member"
            )
        refined = Scalar(StringType(choices=constraint.choices))
    elif isinstance(atom, IntType):
        if any(
            value is not None
            and (not isinstance(value, int) or isinstance(value, bool))
            for value in (constraint.minimum, constraint.maximum)
        ):
            raise TypeError(
                f"driver member constraint for {field.ref.property_id!r} requires "
                "integer bounds"
            )
        refined = Scalar(
            IntType(
                minimum=(
                    atom.minimum
                    if constraint.minimum is None
                    else cast("int", constraint.minimum)
                ),
                maximum=(
                    atom.maximum
                    if constraint.maximum is None
                    else cast("int", constraint.maximum)
                ),
            )
        )
    elif isinstance(atom, FloatType):
        refined = Scalar(
            FloatType(
                minimum=(
                    atom.minimum
                    if constraint.minimum is None
                    else float(constraint.minimum)
                ),
                maximum=(
                    atom.maximum
                    if constraint.maximum is None
                    else float(constraint.maximum)
                ),
            )
        )
    elif isinstance(atom, QuantityType):
        refined = Scalar(
            QuantityType(
                dimension=atom.dimension,
                unit=atom.unit,
                minimum=(
                    atom.minimum
                    if constraint.minimum is None
                    else float(constraint.minimum)
                ),
                maximum=(
                    atom.maximum
                    if constraint.maximum is None
                    else float(constraint.maximum)
                ),
            )
        )
    else:
        raise TypeError(
            f"driver member constraint for {field.ref.property_id!r} requires a "
            "numeric or string member"
        )
    if not is_assignable(refined, declared):
        raise TypeError(
            f"driver member constraint for {field.ref.property_id!r} must narrow "
            "the interface value set"
        )
    if refined == declared:
        raise TypeError(
            f"driver member constraint for {field.ref.property_id!r} is redundant"
        )
    return refined


def _driver_methods(driver_type: type[object]) -> dict[str, object]:
    methods: dict[str, object] = {}
    for base in reversed(driver_type.__mro__):
        if base is object:
            continue
        methods.update(vars(base))
    return methods


def _interface_property_bindings(
    interfaces: Sequence[type[object]],
) -> dict[PropertyRef, DeclaredProperty]:
    return {
        field.ref: field
        for layout in _layouts(interfaces)
        if layout.properties is not None
        for field in layout.properties.fields
    }


def _device_property_bindings(
    driver_type: type[object],
    schema_id: DeviceSchemaId | None,
) -> dict[DevicePropertyRef, DeclaredDeviceProperty]:
    return {
        field.ref: field for field in declared_device_properties(driver_type, schema_id)
    }


def _operation_bindings(
    interfaces: Sequence[type[object]],
) -> dict[OperationRef, DeclaredOperation]:
    return {
        operation.ref: operation
        for layout in _layouts(interfaces)
        for operation in layout.root.operations
    }


def _acquisition_bindings(
    interfaces: Sequence[type[object]],
) -> dict[AcquisitionRef, DeclaredAcquisition]:
    return {
        acquisition.ref: acquisition
        for layout in _layouts(interfaces)
        for acquisition in layout.root.acquisitions
    }


type _DeclaredDriverImplementation = DeclaredOperation | DeclaredAcquisition


@dataclass(frozen=True, slots=True)
class _ResolvedDriverImplementation:
    method_name: str
    declaration: _DeclaredDriverImplementation


def _driver_implementation_bindings(
    driver_type: type[object],
) -> tuple[_ResolvedDriverImplementation, ...]:
    interfaces = _driver_metadata(driver_type).interfaces
    declared_by_identity: dict[int, list[_DeclaredDriverImplementation]] = {}
    expected: list[_DeclaredDriverImplementation] = []
    for layout in _layouts(interfaces):
        for declaration in (
            *layout.root.operations,
            *layout.root.acquisitions,
        ):
            member = cast(
                "object",
                getattr(
                    layout.root.capability_type,
                    declaration.method_name,
                ),
            )
            declared_by_identity.setdefault(id(member), []).append(declaration)
            if not (
                isinstance(declaration, DeclaredAcquisition)
                and declaration.kind == "member_observation"
            ):
                expected.append(declaration)

    claimed: dict[OperationRef | AcquisitionRef, str] = {}
    resolved: list[_ResolvedDriverImplementation] = []
    for method_name, method in _driver_methods(driver_type).items():
        binding = getattr(method, _DRIVER_IMPLEMENTATION_METADATA, None)
        if not isinstance(binding, DriverImplementationBinding):
            continue
        declarations = declared_by_identity.get(id(binding.declaration))
        if declarations is None:
            raise TypeError(
                f"driver implementation {method_name!r} targets a method not "
                "declared by this driver"
            )
        for declaration in declarations:
            if (
                isinstance(declaration, DeclaredAcquisition)
                and declaration.kind == "member_observation"
            ):
                raise TypeError(
                    f"driver implementation {method_name!r} targets member "
                    "observation; member observations use state readers"
                )
            previous = claimed.get(declaration.ref)
            if previous is not None:
                raise TypeError(
                    f"driver methods {previous!r} and {method_name!r} both "
                    f"implement {declaration.ref!r}"
                )
            _validate_driver_implementation_method(
                method_name,
                method,
                declaration,
            )
            claimed[declaration.ref] = method_name
            resolved.append(_ResolvedDriverImplementation(method_name, declaration))

    missing = next(
        (declaration for declaration in expected if declaration.ref not in claimed),
        None,
    )
    if missing is not None:
        kind = "operation" if isinstance(missing, DeclaredOperation) else "acquisition"
        raise TypeError(
            f"driver has no implementation for declared {kind} {missing.method_name!r}"
        )
    return tuple(resolved)


def _validate_driver_implementation_method(
    method_name: str,
    method: object,
    declaration: _DeclaredDriverImplementation,
) -> None:
    if not callable(method):
        raise TypeError(f"driver implementation {method_name!r} must be a method")
    parameters = tuple(signature(method).parameters.values())
    if not parameters or parameters[0].name != "self":
        raise TypeError(
            f"driver implementation {method_name!r} must be an instance method"
        )
    actual = parameters[1:]
    if isinstance(declaration, DeclaredAcquisition):
        if actual:
            raise TypeError(
                f"acquisition implementation {method_name!r} must accept only self"
            )
        return
    expected = tuple(
        (argument.python_name, argument.parameter.kind)
        for argument in declaration.arguments
    )
    observed = tuple((parameter.name, parameter.kind) for parameter in actual)
    if observed != expected:
        raise TypeError(
            f"operation implementation {method_name!r} parameters must match "
            f"declaration {declaration.method_name!r}"
        )


def _implemented_operations(
    driver_type: type[object],
) -> dict[OperationRef, _ResolvedDriverImplementation]:
    return {
        implementation.declaration.ref: implementation
        for implementation in _driver_implementation_bindings(driver_type)
        if isinstance(implementation.declaration, DeclaredOperation)
    }


def _implemented_acquisitions(
    driver_type: type[object],
) -> dict[AcquisitionRef, _ResolvedDriverImplementation]:
    return {
        implementation.declaration.ref: implementation
        for implementation in _driver_implementation_bindings(driver_type)
        if isinstance(implementation.declaration, DeclaredAcquisition)
    }


def _call_operation(
    driver: object,
    implementation: _ResolvedDriverImplementation,
    arguments: Mapping[str, object],
) -> object:
    operation = cast("DeclaredOperation", implementation.declaration)
    positional: list[object] = []
    keywords: dict[str, object] = {}
    for argument in operation.arguments:
        value = arguments[argument.argument_id]
        if argument.parameter.kind is Parameter.POSITIONAL_ONLY:
            positional.append(value)
        else:
            keywords[argument.python_name] = value
    method = cast("Callable[..., object]", getattr(driver, implementation.method_name))
    return method(*positional, **keywords)


def _driver_metadata(driver_type: type[object]) -> InstrumentDriverMetadata:
    metadata = getattr(driver_type, _DRIVER_METADATA, None)
    if not isinstance(metadata, InstrumentDriverMetadata):
        raise TypeError("object instrument driver is missing @instrument_driver")
    return metadata


def _unsupported(
    instrument_id: str,
    kind: str,
    member_id: str,
) -> DriverRejected:
    return DriverRejected(
        problems=(
            problem(
                f"instrument_{kind}_not_implemented",
                f"{instrument_id} does not implement {member_id}",
                phase=ProblemPhase.EXECUTION,
                location=model_location(f"driver_{kind}", member_id),
            ),
        )
    )


__all__ = [
    "Change",
    "DriverImplementationBinding",
    "DriverMemberBinding",
    "DriverMemberConstraint",
    "DriverMemberPolicy",
    "InstrumentDriverMetadata",
    "ObjectInstrumentDriver",
    "Observed",
    "device_member_ref",
    "implements",
    "instrument_driver",
    "member_constraint",
    "member_policy",
    "observed",
    "query",
    "read",
    "update",
    "write",
]

from __future__ import annotations

from typing import Literal, Protocol

import pytest

from scopecat.kernel.value_types import Int, Scalar, String
from scopecat.records.instrument import state_member_target
from scopecat.records.measurement import MeasurementScalar
from scopecat.sdk.instruments import (
    Change,
    DeviceMember,
    DriverAcquisition,
    DriverOperation,
    DriverStatePatch,
    DriverStateReadRequest,
    DriverSuccess,
    Member,
    ObjectInstrumentDriver,
    Observed,
    device_member,
    device_member_ref,
    implements,
    instrument_driver,
    member_constraint,
    member_policy,
    observed,
    query,
    read,
    update,
    write,
)
from scopecat.sdk.instruments.contracts import (
    capture_state_members,
    resolve_state_member_spec,
    restorable_state_members,
)
from scopecat.sdk.instruments.declarations import (
    compile_interface,
    declared_acquisition_ref,
    declared_property_ref,
    declared_result_ref,
    instrument_interface,
    member,
    observation,
    operation,
    write_only_member,
)


@instrument_interface("test.oo_source/v1")
class OOSource(Protocol):
    level: Member[int] = member(
        access="read_write", restore=True, minimum=0, maximum=10
    )
    limit: Member[int] = member(access="read_write", minimum=0, maximum=10)
    serial_number: Member[str] = member(access="read_only", capture=False)
    readback = observation(level, serial_number)

    @operation()
    def zero(self) -> None: ...


@instrument_driver(
    "test.oo_driver",
    "1",
    interfaces=(OOSource,),
    label="OO source",
    device_schema_id="test.oo_source.device/v1",
    device_label="OO source implementation state",
)
class OOSourceDriver(ObjectInstrumentDriver):
    front_panel_locked: DeviceMember[bool] = device_member(
        access="read_write",
        description="Local front-panel lock state",
    )

    def __init__(self) -> None:
        self.instrument_id = "source"
        self._level = 3
        self._limit = 8
        self._front_panel_locked = False
        self.zero_count = 0

    @query(OOSource.level, OOSource.limit, OOSource.serial_number)
    def query_state(self) -> tuple[int, int, Observed[str]]:
        return (
            self._level,
            self._limit,
            observed(
                "SN-1",
                source="configured_fixed",
                evidence={"origin": "fixture"},
            ),
        )

    @update(OOSource.level, OOSource.limit)
    def update_state(
        self,
        *,
        level: Change[int],
        limit: Change[int],
    ) -> None:
        if level.requested:
            self._level = level.value
        if limit.requested:
            self._limit = limit.value

    @read(front_panel_locked)
    def read_front_panel_locked(self) -> bool:
        return self._front_panel_locked

    @write(front_panel_locked)
    def write_front_panel_locked(self, value: bool) -> None:
        self._front_panel_locked = value

    @implements(OOSource.zero)
    def reset_level(self) -> None:
        self._level = 0
        self.zero_count += 1


@instrument_interface("test.write_only_setpoint/v1")
class WriteOnlySetpoint(Protocol):
    target: Member[int] = write_only_member()


@instrument_driver(
    "test.write_only_setpoint",
    "1",
    interfaces=(WriteOnlySetpoint,),
)
class WriteOnlySetpointDriver(ObjectInstrumentDriver):
    def __init__(self) -> None:
        self.instrument_id = "write-only-source"
        self.written: list[int] = []

    @write(WriteOnlySetpoint.target)
    def write_target(self, value: int) -> None:
        self.written.append(value)


def test_member_attributes_compile_without_property_inference() -> None:
    spec = compile_interface(OOSource).spec

    assert [
        (item.id, item.access, item.capture, item.restore) for item in spec.properties
    ] == [
        ("level", "read_write", True, True),
        ("limit", "read_write", True, False),
        ("serial_number", "read_only", False, False),
    ]


def test_object_driver_supports_declared_write_only_state() -> None:
    driver = WriteOnlySetpointDriver()
    target = declared_property_ref(WriteOnlySetpoint, "target")
    [spec] = driver.describe().interfaces

    assert spec.properties[0].access == "write_only"
    assert capture_state_members(driver.describe()) == ()
    assert driver.apply_state(DriverStatePatch(values={target: 7})) == DriverSuccess(
        None
    )
    assert driver.written == [7]


def test_object_driver_adapts_properties_and_methods() -> None:
    driver = OOSourceDriver()
    level = declared_property_ref(OOSource, "level")
    limit = declared_property_ref(OOSource, "limit")
    serial_number = declared_property_ref(OOSource, "serial_number")

    assert driver.describe().interfaces == [compile_interface(OOSource).spec]
    readback = driver.read_state(DriverStateReadRequest(frozenset({level})))
    assert readback.values == {
        level: 3,
        limit: 8,
        serial_number: "SN-1",
    }
    coherence_ids = {observation.coherence_id for observation in readback.observations}
    assert len(coherence_ids) == 1
    assert coherence_ids != {None}
    serial_observation = next(
        observation
        for observation in readback.observations
        if observation.target == serial_number
    )
    assert serial_observation.source == "configured_fixed"
    assert serial_observation.metadata == {"origin": "fixture"}

    applied = driver.apply_state(DriverStatePatch(values={level: 7}))
    assert applied == DriverSuccess(None)
    assert driver._level == 7

    invoked = driver.invoke(
        DriverOperation(target=compile_interface(OOSource).ref.operation("zero"))
    )
    assert invoked == DriverSuccess(None)
    assert driver._level == 0
    assert driver.zero_count == 1


def test_object_driver_projects_member_observations_as_measurements() -> None:
    driver = OOSourceDriver()
    level = declared_result_ref(OOSource, "readback", "level")
    serial_number = declared_result_ref(OOSource, "readback", "serial_number")

    outcome = driver.collect(
        DriverAcquisition(
            target=declared_acquisition_ref(OOSource, "readback"),
            results=frozenset({level, serial_number}),
        )
    )

    assert isinstance(outcome, DriverSuccess)
    assert outcome.value.values == {
        level: MeasurementScalar.create(value=3, dtype="int64"),
        serial_number: MeasurementScalar.create(
            value="SN-1",
            dtype="string",
            metadata={"origin": "fixture"},
        ),
    }
    state_observations = outcome.value.metadata["state_observations"]
    assert isinstance(state_observations, dict)
    assert set(state_observations) == {
        "level",
        "serial_number",
    }


def test_object_driver_captures_and_restores_device_owned_properties() -> None:
    driver = OOSourceDriver()
    target = device_member_ref(OOSourceDriver.front_panel_locked)

    description = driver.describe()
    [device_schema] = description.device_schemas
    assert device_schema.id == "test.oo_source.device/v1"
    assert device_schema.members[0].property.restore is False
    assert driver.read_state(DriverStateReadRequest(frozenset({target}))).values == {
        target: False
    }

    assert driver.apply_state(DriverStatePatch(values={target: True})) == DriverSuccess(
        None
    )
    assert driver._front_panel_locked is True


def test_driver_declaration_requires_complete_member_io_bindings() -> None:
    @instrument_interface("test.missing_bindings/v1")
    class MissingBindings(Protocol):
        value: Member[int] = member(access="read_write")

    with pytest.raises(TypeError, match="no reader"):

        @instrument_driver(
            "test.missing_bindings",
            "1",
            interfaces=(MissingBindings,),
        )
        class MissingBindingsDriver(ObjectInstrumentDriver):
            pass


def test_driver_declaration_requires_explicit_operation_implementations() -> None:
    @instrument_interface("test.missing_operation/v1")
    class MissingOperation(Protocol):
        @operation()
        def arm(self) -> None: ...

    with pytest.raises(TypeError, match=r"no implementation.*arm"):

        @instrument_driver(
            "test.missing_operation",
            "1",
            interfaces=(MissingOperation,),
        )
        class MissingOperationDriver(ObjectInstrumentDriver):
            pass


def test_explicit_implementation_bindings_disambiguate_equal_method_names() -> None:
    @instrument_interface("test.left_action/v1")
    class LeftAction(Protocol):
        @operation()
        def fire(self) -> None: ...

    @instrument_interface("test.right_action/v1")
    class RightAction(Protocol):
        @operation()
        def fire(self) -> None: ...

    @instrument_driver(
        "test.two_actions",
        "1",
        interfaces=(LeftAction, RightAction),
    )
    class TwoActionsDriver(ObjectInstrumentDriver):
        instrument_id = "actions"

        def __init__(self) -> None:
            self.calls: list[str] = []

        @implements(LeftAction.fire)
        def fire_left(self) -> None:
            self.calls.append("left")

        @implements(RightAction.fire)
        def fire_right(self) -> None:
            self.calls.append("right")

    driver = TwoActionsDriver()
    left = compile_interface(LeftAction).ref.operation("fire")
    right = compile_interface(RightAction).ref.operation("fire")

    assert driver.invoke(DriverOperation(target=left)) == DriverSuccess(None)
    assert driver.invoke(DriverOperation(target=right)) == DriverSuccess(None)
    assert driver.calls == ["left", "right"]


def test_object_driver_infers_narrower_interface_property_capabilities() -> None:
    @instrument_interface("test.fixed_setting/v1")
    class FixedSetting(Protocol):
        source: Member[str] = member(access="read_write", restore=True)

    @instrument_driver(
        "test.fixed_setting",
        "1",
        interfaces=(FixedSetting,),
    )
    class FixedSettingDriver(ObjectInstrumentDriver):
        instrument_id = "fixed"

        @read(FixedSetting.source)
        def read_source(self) -> str:
            return "external"

    driver = FixedSettingDriver()
    description = driver.describe()
    target = declared_property_ref(FixedSetting, "source")
    [implementation] = description.interface_property_implementations
    resolved = resolve_state_member_spec(
        description,
        state_member_target(target),
    )

    assert implementation.property.interface_id == target.interface_id
    assert implementation.property.property_id == target.property_id
    assert implementation.access == "read_only"
    assert implementation.capture is True
    assert implementation.restore is False
    assert "value_type" not in implementation.model_dump(mode="json")
    assert resolved is not None
    assert resolved.access == "read_only"
    assert capture_state_members(description) == (target,)
    assert restorable_state_members(description) == frozenset()


def test_object_driver_declares_concrete_member_value_constraints() -> None:
    @instrument_interface("test.constrained_setting/v1")
    class ConstrainedSetting(Protocol):
        count: Member[int] = member(
            access="read_write",
            minimum=0,
            maximum=10,
        )
        mode: Member[Literal["fast", "balanced", "quiet"]] = member(access="read_write")

    @instrument_driver(
        "test.constrained_setting",
        "1",
        interfaces=(ConstrainedSetting,),
        member_constraints=(
            member_constraint(ConstrainedSetting.count, minimum=2, maximum=8),
            member_constraint(
                ConstrainedSetting.mode,
                choices=("balanced", "quiet"),
            ),
        ),
    )
    class ConstrainedSettingDriver(ObjectInstrumentDriver):
        instrument_id = "constrained"

        @query(ConstrainedSetting.count, ConstrainedSetting.mode)
        def query_values(self) -> tuple[int, str]:
            return 5, "balanced"

        @update(ConstrainedSetting.count, ConstrainedSetting.mode)
        def update_values(
            self,
            *,
            count: Change[int],
            mode: Change[str],
        ) -> None:
            del count, mode

    description = ConstrainedSettingDriver().describe()
    count = declared_property_ref(ConstrainedSetting, "count")
    mode = declared_property_ref(ConstrainedSetting, "mode")
    count_spec = resolve_state_member_spec(description, state_member_target(count))
    mode_spec = resolve_state_member_spec(description, state_member_target(mode))

    assert count_spec is not None
    assert count_spec.value_type == Scalar(Int(minimum=2, maximum=8))
    assert mode_spec is not None
    assert mode_spec.value_type == Scalar(String(choices=("balanced", "quiet")))
    assert all(
        "value_type" in implementation.model_dump(mode="json")
        for implementation in description.interface_property_implementations
    )
    declared = compile_interface(ConstrainedSetting).spec
    assert declared.properties[0].value_type == Scalar(Int(minimum=0, maximum=10))


def test_object_driver_rejects_member_constraint_widening() -> None:
    @instrument_interface("test.invalid_constraint/v1")
    class InvalidConstraint(Protocol):
        count: Member[int] = member(access="read_only", minimum=0)

    with pytest.raises(TypeError, match="must narrow the interface value set"):

        @instrument_driver(
            "test.invalid_constraint",
            "1",
            interfaces=(InvalidConstraint,),
            member_constraints=(
                member_constraint(InvalidConstraint.count, minimum=-1),
            ),
        )
        class InvalidConstraintDriver(ObjectInstrumentDriver):
            @read(InvalidConstraint.count)
            def read_count(self) -> int:
                return 1


def test_object_driver_applies_exception_only_member_lifecycle_policies() -> None:
    @instrument_interface("test.lifecycle_policy/v1")
    class LifecyclePolicy(Protocol):
        record_only: Member[int] = member(access="read_write", restore=True)
        on_demand: Member[int] = member(access="read_write", restore=True)

    @instrument_driver(
        "test.lifecycle_policy",
        "1",
        interfaces=(LifecyclePolicy,),
        member_policies=(
            member_policy(LifecyclePolicy.record_only, restore=False),
            member_policy(LifecyclePolicy.on_demand, capture=False),
        ),
    )
    class LifecyclePolicyDriver(ObjectInstrumentDriver):
        instrument_id = "lifecycle"

        @query(LifecyclePolicy.record_only, LifecyclePolicy.on_demand)
        def query_values(self) -> tuple[int, int]:
            return 1, 2

        @update(LifecyclePolicy.record_only, LifecyclePolicy.on_demand)
        def update_values(
            self,
            *,
            record_only: Change[int],
            on_demand: Change[int],
        ) -> None:
            del record_only, on_demand

    description = LifecyclePolicyDriver().describe()
    record_only = declared_property_ref(LifecyclePolicy, "record_only")
    on_demand = declared_property_ref(LifecyclePolicy, "on_demand")
    record_only_spec = resolve_state_member_spec(
        description,
        state_member_target(record_only),
    )
    on_demand_spec = resolve_state_member_spec(
        description,
        state_member_target(on_demand),
    )

    assert record_only_spec is not None
    assert (
        record_only_spec.access,
        record_only_spec.capture,
        record_only_spec.restore,
    ) == (
        "read_write",
        True,
        False,
    )
    assert on_demand_spec is not None
    assert (on_demand_spec.access, on_demand_spec.capture, on_demand_spec.restore) == (
        "read_write",
        False,
        False,
    )
    assert capture_state_members(description) == (record_only,)
    assert restorable_state_members(description) == frozenset()


def test_object_driver_rejects_policy_made_redundant_by_io_bindings() -> None:
    @instrument_interface("test.redundant_policy/v1")
    class RedundantPolicy(Protocol):
        fixed: Member[int] = member(access="read_write", restore=True)

    with pytest.raises(TypeError, match="implementation is already read-only"):

        @instrument_driver(
            "test.redundant_policy",
            "1",
            interfaces=(RedundantPolicy,),
            member_policies=(member_policy(RedundantPolicy.fixed, restore=False),),
        )
        class RedundantPolicyDriver(ObjectInstrumentDriver):
            @read(RedundantPolicy.fixed)
            def read_fixed(self) -> int:
                return 1

"""Declared field updates and identity-preserving composite-key references."""

from typing import assert_type

import scopecat as sc
from scopecat.program.value_refs import internal_value_ref_parameter_lookup


class Device(sc.ParameterModel, table="device_parameters"):
    device: sc.Param[sc.EntityRef] = sc.param(key=True, entity_kind="logical_device")
    frequency: sc.Magnitude[float] = sc.quantity(unit="GHz")
    enabled: sc.Param[bool] = sc.param()


def test_declared_update_uses_the_same_key_and_unit_as_experiment_reference() -> None:
    ref = sc.parameter_ref(Device.frequency, "q0")
    assert_type(ref, sc.ValueRef[sc.Quantity])
    update = sc.parameter_update(Device.frequency, "q0", 5.1)
    assert update.key == {"device": sc.EntityRef(id="q0", kind="logical_device")}
    assert update.values == {"frequency": sc.Quantity(5.1, "GHz")}
    lookup = internal_value_ref_parameter_lookup(ref)
    assert lookup is not None
    assert lookup[0].table_id == update.parameter_id
    assert lookup[0].column_id == "frequency"


def test_composite_references_preserve_entities_when_selection_order_changes() -> None:
    class Bias(sc.ParameterModel, table="bias"):
        profile: sc.Param[str] = sc.param(key=True)
        qubit: sc.Param[sc.EntityRef] = sc.param(key=True, entity_kind="logical_qubit")
        voltage: sc.Magnitude[float] = sc.quantity(unit="V")

    q0 = sc.EntityRef(id="q0", kind="logical_qubit")
    q1 = sc.EntityRef(id="q1", kind="logical_qubit")
    refs = sc.PerEntity(
        (qubit, sc.parameter_ref(Bias.voltage, ("parked", qubit)))
        for qubit in sc.each(q1, q0)
    )
    lookup = internal_value_ref_parameter_lookup(refs[q0])
    assert lookup is not None
    assert dict(lookup[1]) == {"profile": "parked", "qubit": q0}

"""A device-independent parameter table preserves typed entity identities."""

from scopecat.api.parameters import ParameterTable, _TableData
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.value_types import Entity, Float, Scalar, Table, TableColumn


def test_entity_keys_accept_plain_ids_without_rewriting_stored_keys() -> None:
    schema = Table(
        columns=(
            TableColumn("qubit", Scalar(Entity(entity_kind="qubit"))),
            TableColumn("gain", Scalar(Float())),
        ),
        primary_key=("qubit",),
    )
    data = _TableData("drive", schema)
    key = EntityRef(id="q0", kind="qubit")
    data.load(({"qubit": key, "gain": 0.1},))
    table = ParameterTable(data)
    assert table["q0"]["qubit"] == key
    table["q0"] = {"qubit": "q0", "gain": 0.2}
    assert table[key]["gain"] == 0.2
    assert table["q0"]["qubit"] == key
    table["q1"] = {"gain": 0.3}
    assert table["q1"]["qubit"] == EntityRef(id="q1", kind="qubit")
    del table["q1"]
    assert len(table) == 1

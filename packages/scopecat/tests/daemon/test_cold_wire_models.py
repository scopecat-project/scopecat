"""First-use wire validation works without eagerly building the entire client."""

import subprocess
import sys


def test_cold_commands_and_nested_views_preserve_validation_and_wire_shape() -> None:
    script = """
from pydantic import ValidationError
from scopecat.daemon.views import MeasurementArrowQuery
from scopecat.daemon.wire import RunCoverageAdvanceCommand
assert not RunCoverageAdvanceCommand.__pydantic_complete__
assert not MeasurementArrowQuery.__pydantic_complete__
command = RunCoverageAdvanceCommand.model_validate_json(
    '{"lease_id":"lease","start_index":0,"point_count":1}'
)
assert command.model_dump() == {"lease_id": "lease", "start_index": 0, "point_count": 1}
column = {"name": "value", "variable_id": "signal"}
query = MeasurementArrowQuery.model_validate({"columns": [column]})
assert MeasurementArrowQuery.model_validate_json(query.model_dump_json()) == query
try:
    RunCoverageAdvanceCommand.model_validate(
        {"lease_id": "lease", "start_index": 0, "point_count": 0}
    )
except ValidationError as error:
    assert error.errors()[0]["loc"] == ("point_count",)
else:
    raise AssertionError("invalid progress accepted")
try:
    MeasurementArrowQuery.model_validate({"columns": [column]*2})
except ValidationError as error:
    assert "must be unique" in str(error)
else:
    raise AssertionError("duplicate projection accepted")
try:
    command.point_count = 2
except ValidationError as error:
    assert error.errors()[0]["type"] == "frozen_instance"
else:
    raise AssertionError("wire command became mutable")
"""
    completed = subprocess.run(  # noqa: S603 - fixed cold-interpreter test
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr

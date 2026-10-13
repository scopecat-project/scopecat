from unittest.mock import patch
from uuid import UUID

import pytest

from scopecat.kernel.ids import new_run_id, validate_run_id


def test_run_ids_use_complete_canonical_uuid7() -> None:
    value = new_run_id()
    parsed = UUID(value)
    assert parsed.version == 7
    assert str(parsed) == value
    assert len(value) == 36
    validate_run_id(value)


@pytest.mark.parametrize(
    "expected",
    [
        "0199a9c0-0000-7000-8000-000000000001",
        "0199abcd-1234-7fed-b987-654321abcdef",
    ],
)
def test_run_id_preserves_the_complete_standard_library_uuid(expected: str) -> None:
    with patch("scopecat.kernel.ids.uuid7", return_value=UUID(expected)) as generate:
        assert new_run_id() == expected
        generate.assert_called_once_with()


@pytest.mark.parametrize(
    "value",
    [
        "",
        "run_20261013T120000Z_12345678",
        "run-1",
        "../run",
        "0199a9c0-0000-4000-8000-000000000001",
        "0199a9c0000070008000000000000001",
        "0199A9C0-0000-7000-8000-000000000001",
        "{0199a9c0-0000-7000-8000-000000000001}",
        "urn:uuid:0199a9c0-0000-7000-8000-000000000001",
        "0199a9c0-0000-7000-8000-000000000001\n",
        "0199a9c0-0000-7000-0000-000000000001",
    ],
)
def test_run_id_validation_rejects_noncanonical_or_non_v7_identity(value: str) -> None:
    with pytest.raises(ValueError, match="canonical UUIDv7"):
        validate_run_id(value)

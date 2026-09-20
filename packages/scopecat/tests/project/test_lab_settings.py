"""Local settings are explicit deployment inputs, never ambient defaults."""

from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict

from scopecat.lab_settings import (
    LabSettingsError,
    lab_settings_identity,
    read_lab_settings,
)
from scopecat.runtime_binding import RuntimeBindingError, load_runtime_binding


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    initial_configuration: Literal["simulator", "physical"] = "simulator"


def bind(root: Path, filename: str) -> None:
    (root / "scopecat.runtime.toml").write_text(
        '[runtime]\ndata_root=".data"\ndeployment_root=".deployment"\n'
        f'settings_file="{filename}"\n',
        encoding="utf-8",
    )


def test_absent_selection_uses_adapter_defaults(tmp_path: Path) -> None:
    assert load_runtime_binding(tmp_path).settings_file is None
    assert read_lab_settings(tmp_path, Settings) == Settings()
    assert lab_settings_identity(tmp_path) is None


def test_selected_settings_are_frozen_per_read(tmp_path: Path) -> None:
    bind(tmp_path, "local.json")
    settings = tmp_path / "local.json"
    settings.write_text('{"initial_configuration":"physical"}', encoding="utf-8")
    assert load_runtime_binding(tmp_path).settings_file == settings.resolve()
    selected = read_lab_settings(tmp_path, Settings)
    before = lab_settings_identity(tmp_path)
    assert before == lab_settings_identity(tmp_path)
    settings.write_text('{"initial_configuration":"simulator"}', encoding="utf-8")
    assert lab_settings_identity(tmp_path) != before
    assert selected.initial_configuration == "physical"
    assert read_lab_settings(tmp_path, Settings).initial_configuration == "simulator"


def test_identity_includes_selected_path(tmp_path: Path) -> None:
    for name in ("one.json", "two.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    bind(tmp_path, "one.json")
    first = lab_settings_identity(tmp_path)
    bind(tmp_path, "two.json")
    assert lab_settings_identity(tmp_path) != first


@pytest.mark.parametrize("content", [None, "not json", "[]"])
def test_invalid_file_never_uses_defaults(tmp_path: Path, content: str | None) -> None:
    bind(tmp_path, "local.json")
    if content is not None:
        (tmp_path / "local.json").write_text(content, encoding="utf-8")
    with pytest.raises(LabSettingsError, match=r"local\.json"):
        read_lab_settings(tmp_path, Settings)
    with pytest.raises(LabSettingsError, match=r"local\.json"):
        lab_settings_identity(tmp_path)


def test_adapter_validation_explains_source(tmp_path: Path) -> None:
    bind(tmp_path, "local.json")
    (tmp_path / "local.json").write_text('{"ambient_mode":true}', encoding="utf-8")
    with pytest.raises(LabSettingsError, match=r"local\.json"):
        read_lab_settings(tmp_path, Settings)


def test_empty_settings_path_is_rejected(tmp_path: Path) -> None:
    bind(tmp_path, "")
    with pytest.raises(RuntimeBindingError, match="settings_file"):
        load_runtime_binding(tmp_path)

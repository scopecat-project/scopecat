from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from scopecat.application import LabBootstrap
from scopecat.config.documents import (
    load_config_snapshot_document,
    parse_config_snapshot_document,
)
from scopecat.kernel.errors import CheckFailed
from scopecat.project import Project
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.setup import ExecutableSetupSnapshot
from scopecat_testkit.config_registry import bootstrap_declaration, parameter_content

from scopecat_server.config_commands import load_source_config

_CONFIG_FIXTURE = (
    Path(__file__).parents[3]
    / "fixtures"
    / "core"
    / "simple_scan"
    / "config-snapshot.json"
)


def test_config_snapshot_rejects_previous_external_format() -> None:
    document = json.loads(_CONFIG_FIXTURE.read_text(encoding="utf-8"))
    document["format_version"] = "scopecat.config_snapshot.v10"

    with pytest.raises(ValueError, match=r"scopecat\.config_snapshot\.v11"):
        parse_config_snapshot_document(json.dumps(document))


def test_source_config_is_freshly_built_and_validated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    baseline = load_config_snapshot_document(_CONFIG_FIXTURE)
    calls = 0

    def bootstrap_config() -> ConfigProfileSnapshot:
        nonlocal calls
        calls += 1
        return baseline.model_copy(update={"id": f"source-{calls}"})

    monkeypatch.setattr(
        Project,
        "load_bootstrap",
        _bootstrap_loader(
            LabBootstrap(
                setup=lambda: ExecutableSetupSnapshot.from_config(
                    load_config_snapshot_document(_CONFIG_FIXTURE)
                ),
                parameter_defaults=lambda: parameter_content(bootstrap_config()),
            )
        ),
    )

    first = load_source_config(project)
    second = load_source_config(project)

    assert first.id == "source-1"
    assert second.id == "source-2"
    assert calls == 2


def test_source_config_rejects_missing_or_invalid_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(
        Project,
        "load_bootstrap",
        _bootstrap_loader(LabBootstrap()),
    )

    with pytest.raises(
        ValueError,
        match="project bootstrap must define setup and parameter_defaults",
    ):
        load_source_config(project)

    config = load_config_snapshot_document(_CONFIG_FIXTURE)
    invalid_route = config.routing.routes[0].model_copy(
        update={"instrument_id": "missing-source"}
    )
    invalid = config.model_copy(
        update={
            "system": config.system.model_copy(
                update={
                    "routing": config.routing.model_copy(
                        update={"routes": [invalid_route]}
                    )
                }
            )
        }
    )
    monkeypatch.setattr(
        Project,
        "load_bootstrap",
        _bootstrap_loader(bootstrap_declaration(invalid)),
    )

    with pytest.raises(CheckFailed):
        load_source_config(project)


def _project(root: Path) -> Project:
    manifest = root / "scopecat.toml"
    manifest.write_text("[lab]\n", encoding="utf-8")
    return Project(
        root=root,
        manifest=manifest,
        bootstrap_spec=None,
        application_spec=None,
        instrument_backend_spec=None,
    )


def _bootstrap_loader(
    bootstrap: LabBootstrap,
) -> Callable[[Project], LabBootstrap]:
    def load_bootstrap(_project: Project) -> LabBootstrap:
        return bootstrap

    return load_bootstrap

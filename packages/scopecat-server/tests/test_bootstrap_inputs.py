"""First-use declarations keep equipment separate from parameter ownership."""

from pathlib import Path

import pytest
from scopecat.application import LabBootstrap
from scopecat.config.registry.records import ParameterConfigRegistrySource
from scopecat.records.parameter_revision import ParameterRevisionContent
from scopecat.records.setup import ExecutableSetupSnapshot
from scopecat_testkit.config_registry import parameter_content
from scopecat_testkit.workflow_fixtures import load_config

from scopecat_server import BackendConflict, LocalDaemonRuntime
from scopecat_server.services.config import ConfigService


def _declare(monkeypatch: pytest.MonkeyPatch, inputs: LabBootstrap) -> None:
    def factory(_root: Path) -> LabBootstrap:
        return inputs

    def load_factory(*_args: object, **_kwargs: object) -> object:
        return factory

    monkeypatch.setattr("scopecat_server.runtime.load_bootstrap_factory", load_factory)


def test_equipment_only_bootstrap_never_creates_parameter_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    equipment = ExecutableSetupSnapshot.from_config(load_config())
    calls = 0

    def setup() -> ExecutableSetupSnapshot:
        nonlocal calls
        calls += 1
        return equipment

    _declare(monkeypatch, LabBootstrap(setup=setup))
    for _ in range(2):
        with LocalDaemonRuntime(tmp_path, bootstrap_spec="test:bootstrap") as runtime:
            assert runtime.application.setup.current().revision.setup == equipment
            assert runtime.application.config.get_config_registry().entries == ()
    assert calls == 1


def test_separate_defaults_are_evaluated_once_and_keep_exact_setup_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config()
    equipment = ExecutableSetupSnapshot.from_config(config)
    calls: list[str] = []

    def setup() -> ExecutableSetupSnapshot:
        calls.append("setup")
        return equipment

    def parameters() -> ParameterRevisionContent:
        calls.append("parameters")
        return parameter_content(config)

    _declare(monkeypatch, LabBootstrap(setup=setup, parameter_defaults=parameters))
    for _ in range(2):
        with LocalDaemonRuntime(tmp_path, bootstrap_spec="test:bootstrap") as runtime:
            active = runtime.application.config.get_active_config()
            assert active.config == config
            assert runtime.application.setup.current().revision.setup == equipment
            assert isinstance(active.entry.source, ParameterConfigRegistrySource)
            assert (
                active.entry.source.setup
                == runtime.application.setup.current().revision.ref
            )
    assert calls == ["setup", "parameters"]


def test_parameter_factory_failure_does_not_create_equipment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def parameters() -> ParameterRevisionContent:
        raise ValueError("invalid parameter source")

    _declare(
        monkeypatch,
        LabBootstrap(
            setup=lambda: ExecutableSetupSnapshot.from_config(load_config()),
            parameter_defaults=parameters,
        ),
    )
    with pytest.raises(ValueError, match="invalid parameter source"):
        LocalDaemonRuntime(tmp_path, bootstrap_spec="test:bootstrap")
    with LocalDaemonRuntime(tmp_path) as runtime:
        assert runtime.application.setup.list() == ()
        assert runtime.application.config.get_config_registry().entries == ()


def test_parameter_default_declaration_cannot_supply_equipment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _declare(
        monkeypatch,
        LabBootstrap(parameter_defaults=lambda: parameter_content(load_config())),
    )
    with pytest.raises(BackendConflict, match="require an initial setup"):
        LocalDaemonRuntime(tmp_path, bootstrap_spec="test:bootstrap")


def test_interrupted_declared_bootstrap_keeps_setup_and_does_not_reevaluate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def parameters() -> ParameterRevisionContent:
        nonlocal calls
        calls += 1
        return parameter_content(load_config())

    _declare(
        monkeypatch,
        LabBootstrap(
            setup=lambda: ExecutableSetupSnapshot.from_config(load_config()),
            parameter_defaults=parameters,
        ),
    )

    def fail_publish(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("parameter publication interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(ConfigService, "publish_config", fail_publish)
        with pytest.raises(RuntimeError, match="publication interrupted"):
            LocalDaemonRuntime(tmp_path, bootstrap_spec="test:bootstrap")
    with pytest.raises(BackendConflict, match="explicitly complete initialization"):
        LocalDaemonRuntime(tmp_path, bootstrap_spec="test:bootstrap")
    assert calls == 1
    with LocalDaemonRuntime(tmp_path) as runtime:
        assert runtime.application.setup.current().activation.generation == 1
        assert runtime.application.config.get_config_registry().entries == ()

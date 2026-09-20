from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest
from scopecat_testkit.project_loading import isolated_project_imports

from scopecat.api.lab import LabClient
from scopecat.application.bootstrap import LabBootstrap
from scopecat.application.lab import LabApplication
from scopecat.daemon.endpoint import (
    DAEMON_URL_ENV,
    DaemonEndpointError,
    DaemonEndpointRecord,
    daemon_record_path,
)
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.planning.system import ExperimentSystem
from scopecat.project import (
    ProjectCodeLoadError,
    ProjectManifestError,
    load_application_factory,
    load_bootstrap_factory,
    load_project,
    open_project,
)
from scopecat.records.config import ConfigProfileSnapshot


@pytest.fixture(autouse=True)
def isolate_project_loader(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("scopecat.daemon.endpoint.verify_daemon_binding", Mock())
    with isolated_project_imports():
        yield


def test_project_composition_is_resolved_from_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "scopecat.toml"
    manifest.write_text(
        "[lab]\n"
        'bootstrap = "my_lab.application:create_bootstrap"\n'
        'application = "my_lab.application:create"\n'
        'instrument_backend = "my_lab.backend:create"\n',
        encoding="utf-8",
    )

    project = load_project(manifest)

    assert project.root == tmp_path
    assert project.bootstrap_spec == "my_lab.application:create_bootstrap"
    assert project.application_spec == "my_lab.application:create"
    assert project.instrument_backend_spec == "my_lab.backend:create"


def test_project_is_discovered_from_a_child_directory(tmp_path: Path) -> None:
    (tmp_path / "scopecat.toml").write_text(
        '[lab]\napplication = "my_lab.application:create"\n',
        encoding="utf-8",
    )
    child = tmp_path / "notebooks" / "calibration"
    child.mkdir(parents=True)

    assert open_project(child).root == tmp_path


def test_empty_lab_manifest_can_open_before_code_or_config_exists(
    tmp_path: Path,
) -> None:
    (tmp_path / "scopecat.toml").write_text("[lab]\n", encoding="utf-8")

    project = open_project(tmp_path)
    client = project.connect("http://daemon.local")

    assert project.application_spec is None
    assert project.bootstrap_spec is None
    assert project.instrument_backend_spec is None
    assert isinstance(project.load_bootstrap(), LabBootstrap)
    assert isinstance(project.load_application(), LabApplication)
    assert isinstance(client, LabClient)
    client.close()


def test_project_connect_forwards_notebook_operator(
    tmp_path: Path,
) -> None:
    (tmp_path / "scopecat.toml").write_text("[lab]\n", encoding="utf-8")

    client = open_project(tmp_path).connect(
        "http://daemon.local",
        operator="alice",
    )

    assert client._instruments._operator == "alice"
    client.close()


def test_project_connect_overrides_the_notebook_system_builder(
    tmp_path: Path,
) -> None:
    (tmp_path / "scopecat.toml").write_text("[lab]\n", encoding="utf-8")

    def build_experiment_system(
        config: ConfigProfileSnapshot,
        instrument_catalog: InstrumentContractCatalog,
    ) -> ExperimentSystem:
        del config
        return ExperimentSystem(instrument_catalog=instrument_catalog)

    client = open_project(tmp_path).connect(
        "http://daemon.local",
        build_experiment_system=build_experiment_system,
    )

    assert client._runner.build_experiment_system is build_experiment_system
    client.close()


def test_project_connect_prioritizes_explicit_then_environment_then_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "scopecat.toml").write_text("[lab]\n", encoding="utf-8")
    project = open_project(tmp_path)
    record = DaemonEndpointRecord(
        project_root=tmp_path,
        data_root=tmp_path / ".scopecat",
        deployment_root=tmp_path / ".scopecat",
        pid=123,
        process_create_time=1,
        base_url="http://record.local:3000",
        shutdown_token="test-shutdown-token" * 2,
        started_at=datetime.now(UTC),
    )
    path = daemon_record_path(tmp_path)
    path.parent.mkdir()
    path.write_text(record.model_dump_json(), encoding="utf-8")

    monkeypatch.setenv(DAEMON_URL_ENV, "http://environment.local:2000")
    explicit = project.connect("http://explicit.local:1000")
    environment = project.connect()
    monkeypatch.delenv(DAEMON_URL_ENV)
    discovered = project.connect()

    assert str(explicit._client._http.base_url) == "http://explicit.local:1000"
    assert str(environment._client._http.base_url) == ("http://environment.local:2000")
    assert str(discovered._client._http.base_url) == "http://record.local:3000"
    explicit.close()
    environment.close()
    discovered.close()


def test_project_connect_does_not_guess_a_fixed_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "scopecat.toml").write_text("[lab]\n", encoding="utf-8")
    monkeypatch.delenv(DAEMON_URL_ENV, raising=False)

    with pytest.raises(DaemonEndpointError, match="scopecat start"):
        open_project(tmp_path).connect()


def test_bootstrap_and_application_are_imported_from_project_src(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "src" / "project_application_fixture"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "application.py").write_text(
        (
            "from scopecat.application import LabBootstrap\n\n"
            "def lazy_bootstrap():\n"
            "    from project_application_bootstrap import CONFIG\n"
            "    return CONFIG\n\n"
            "def create_bootstrap(_project):\n"
            "    return LabBootstrap(bootstrap_config=lazy_bootstrap)\n\n"
            "def create(_project):\n"
            "    from scopecat.application import LabApplication\n"
            "    from project_automation_callbacks import CALLBACK_MARKER\n"
            "    assert CALLBACK_MARKER == 'loaded'\n"
            "    return LabApplication()\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "src" / "project_application_bootstrap.py").write_text(
        "CONFIG = {'id': 'lazy-project-config'}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "project_automation_callbacks.py").write_text(
        "CALLBACK_MARKER = 'loaded'\n",
        encoding="utf-8",
    )
    (tmp_path / "scopecat.toml").write_text(
        "[lab]\n"
        'bootstrap = "project_application_fixture.application:create_bootstrap"\n'
        'application = "project_application_fixture.application:create"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "path", sys.path.copy())
    original_path = tuple(sys.path)

    bootstrap_factory = load_bootstrap_factory(
        "project_application_fixture.application:create_bootstrap",
        tmp_path,
    )
    repeated_bootstrap = load_bootstrap_factory(
        "project_application_fixture.application:create_bootstrap",
        tmp_path,
    )
    factory = load_application_factory(
        "project_application_fixture.application:create",
        tmp_path,
    )
    repeated = load_application_factory(
        "project_application_fixture.application:create",
        tmp_path,
    )

    bootstrap = bootstrap_factory(tmp_path)
    assert "project_automation_callbacks" not in sys.modules
    application = factory(tmp_path)
    assert "project_automation_callbacks" in sys.modules
    assert isinstance(bootstrap, LabBootstrap)
    assert isinstance(application, LabApplication)
    assert bootstrap.bootstrap_config is not None
    assert bootstrap.bootstrap_config() == {"id": "lazy-project-config"}
    assert isinstance(repeated_bootstrap(tmp_path), LabBootstrap)
    assert isinstance(repeated(tmp_path), LabApplication)
    assert str(tmp_path / "src") in sys.path
    assert str(tmp_path) in sys.path
    assert tuple(sys.path) != original_path
    client = open_project(tmp_path).connect("http://daemon.local")
    assert isinstance(client, LabClient)
    client.close()


def test_different_projects_cannot_reuse_the_same_application_module(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    module_name = "shared_project_application_fixture"
    _write_application_module(first, module_name, marker="first")
    _write_application_module(second, module_name, marker="second")
    first_factory = load_application_factory(f"{module_name}.application:create", first)

    assert isinstance(first_factory(first), LabApplication)
    with pytest.raises(
        ProjectCodeLoadError,
        match="already loaded project code",
    ) as caught:
        load_application_factory(f"{module_name}.application:create", second)

    assert str(first.resolve()) in str(caught.value)
    assert str(second.resolve()) in str(caught.value)
    assert "separate process" in str(caught.value)
    loaded_module = sys.modules[f"{module_name}.application"]
    assert vars(loaded_module)["PROJECT_MARKER"] == "first"
    assert str(first / "src") in sys.path
    assert str(second / "src") not in sys.path


def test_preloaded_application_module_from_outside_project_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "conflicting_project_application_fixture"
    _write_application_module(tmp_path, module_name, marker="project")
    conflicting = ModuleType(module_name)
    conflicting.__file__ = str(tmp_path.parent / "foreign" / "__init__.py")
    monkeypatch.setitem(sys.modules, module_name, conflicting)

    with pytest.raises(
        ProjectCodeLoadError,
        match="already loaded from outside this project",
    ):
        load_application_factory(f"{module_name}.application:create", tmp_path)


@pytest.mark.parametrize(
    "content, message",
    [
        ("", r"requires a \[lab\] table"),
        (
            '[lab]\nbootstrap-config = "config/initial.json"\n',
            r"unknown \[lab\] field",
        ),
        ("[lab]\nbootstrap = ''\n", "must be a non-empty string"),
        ("[lab]\napplication = ''\n", "must be a non-empty string"),
        ("[lab]\ninstrument_backend = ''\n", "must be a non-empty string"),
    ],
)
def test_invalid_project_manifests_fail_at_the_boundary(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    manifest = tmp_path / "scopecat.toml"
    manifest.write_text(content, encoding="utf-8")

    with pytest.raises(ProjectManifestError, match=message):
        load_project(manifest)


@pytest.mark.parametrize("spec", ["factory", ":factory", "module:"])
def test_invalid_application_specs_fail_at_the_boundary(
    tmp_path: Path,
    spec: str,
) -> None:
    with pytest.raises(ValueError, match="MODULE:CALLABLE"):
        load_application_factory(spec, tmp_path)


@pytest.mark.parametrize("spec", ["factory", ":factory", "module:"])
def test_invalid_bootstrap_specs_fail_at_the_boundary(
    tmp_path: Path,
    spec: str,
) -> None:
    with pytest.raises(ValueError, match="MODULE:CALLABLE"):
        load_bootstrap_factory(spec, tmp_path)


def _write_application_module(root: Path, module_name: str, *, marker: str) -> None:
    package = root / "src" / module_name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "application.py").write_text(
        (
            "from scopecat.application import LabApplication\n\n"
            f"PROJECT_MARKER = {marker!r}\n\n"
            "def create(_project):\n"
            "    return LabApplication()\n"
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "missing", ["pilot_missing_dependency", "pilot_missing_dependency.child"]
)
def test_missing_application_dependency_names_module_and_preserves_cause(
    tmp_path: Path,
    missing: str,
) -> None:
    source = tmp_path / "src" / "pilot_lab.py"
    source.parent.mkdir()
    source.write_text(f"raise ModuleNotFoundError('missing', name={missing!r})\n")
    with pytest.raises(
        ProjectCodeLoadError, match="application dependencies"
    ) as raised:
        load_application_factory("pilot_lab:create", tmp_path)
    assert missing in str(raised.value)
    assert isinstance(raised.value.__cause__, ModuleNotFoundError)
    assert raised.value.__cause__.name == missing


def test_missing_application_attribute_is_not_reported_as_dependency(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "pilot_lab.py"
    source.parent.mkdir()
    source.write_text("# Application callable is absent.\n")
    with pytest.raises(AttributeError, match="create"):
        load_application_factory("pilot_lab:create", tmp_path)


@pytest.mark.parametrize(
    "declaration, message",
    [
        (
            '[lab]\napplication="demo:create"\n[lab.capabilities]\n',
            "mutually exclusive",
        ),
        ('[lab.capabilities]\nunknown="demo:value"\n', "unknown"),
        ('[lab.capabilities]\nprocedures="demo:value"\n', "list of import names"),
        ('[lab.capabilities]\nexperiment_system="demo"\n', "invalid"),
        ('[lab.capabilities]\nauthor_modules=["demo:value"]\n', "invalid"),
    ],
)
def test_capability_manifest_rejects_ambiguous_or_invalid_declarations(
    tmp_path: Path, declaration: str, message: str
) -> None:
    manifest = tmp_path / "scopecat.toml"
    manifest.write_text(declaration)
    with pytest.raises(ProjectManifestError, match=message):
        load_project(manifest)


def test_declared_capabilities_compose_only_when_execution_is_loaded(
    tmp_path: Path,
) -> None:
    from scopecat.author_workspaces import author_workspace_id
    from scopecat.project_sources import loading_workspace

    package = tmp_path / "src" / "declared_lab"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "execution.py").write_text(
        "from pydantic import BaseModel\n"
        "from scopecat.automation import procedure, CalibrationRegistry\n"
        "from scopecat.project_sources import loading_workspace\n"
        "WORKSPACE = loading_workspace.get()\n"
        "class Intent(BaseModel, frozen=True): pass\n"
        "@procedure(id='declared.test', version='1', intent=Intent)\n"
        "def run(context: object, intent: Intent) -> None: pass\n"
        "CALIBRATIONS = CalibrationRegistry()\n"
        "def build(config, catalog): return (config, catalog)\n"
    )
    (tmp_path / "scopecat.toml").write_text(
        "[lab.capabilities]\n"
        'author_modules=["declared_lab.execution"]\n'
        'experiment_system="declared_lab.execution:build"\n'
        'procedures=["declared_lab.execution:run"]\n'
        'calibrations="declared_lab.execution:CALIBRATIONS"\n'
    )
    project = open_project(tmp_path)
    project.load_bootstrap()
    assert "declared_lab.execution" not in sys.modules
    prior_workspace = loading_workspace.get()
    application = project.load_application()
    module = sys.modules["declared_lab.execution"]
    assert application.build_experiment_system is module.build
    assert application.calibrations is module.CALIBRATIONS
    assert tuple(application.procedures.values()) == (module.run,)
    assert author_workspace_id(tmp_path) == module.WORKSPACE
    assert loading_workspace.get() == prior_workspace


def test_declared_capabilities_resolve_from_captured_code_root(tmp_path: Path) -> None:
    from dataclasses import replace

    live = tmp_path / "live"
    captured = tmp_path / "captured"
    for directory in (live, captured):
        (directory / "src").mkdir(parents=True)
        (directory / "scopecat.toml").write_text(
            '[lab.capabilities]\nexperiment_system="captured_lab:build"\n'
        )
    (live / "src" / "captured_lab.py").write_text("raise RuntimeError('live code')\n")
    (captured / "src" / "captured_lab.py").write_text(
        "def build(config, catalog): return (config, catalog)\n"
    )
    application = replace(open_project(live), code_root=captured).load_application()
    assert application.build_experiment_system is not None
    filename = sys.modules["captured_lab"].__file__
    assert filename is not None
    assert Path(filename).is_relative_to(captured)

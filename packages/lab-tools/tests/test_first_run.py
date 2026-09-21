"""First-run placement preserves existing laboratory code and evidence."""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from lab_tools import first_run
from scopecat.project import load_project
from scopecat.runtime_binding import RUNTIME_BINDING_NAME, load_runtime_binding


@pytest.fixture
def registration(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(first_run, "select_static_dir", lambda **_: tmp_path / "gui")

    def register(self, root, python, **kwargs):
        calls.append((root, python, kwargs))
        return "registered"

    monkeypatch.setattr(first_run.Services, "register", register)
    return calls


def test_create_standard_experiment_with_separate_data(tmp_path, registration):
    project = tmp_path / "experiment"
    data = tmp_path / "scientific-data"
    result = first_run.setup(
        tmp_path / "home",
        first_run.SetupRequest(
            mode="create", project=str(project), data_root=str(data)
        ),
    )
    assert result == "registered"
    loaded = load_project(project / "scopecat.toml")
    assert loaded.capabilities is not None
    assert loaded.runtime_binding.data_root == data
    assert loaded.runtime_binding.deployment_root == project / ".scopecat"
    assert not data.exists()
    assert not (project / ".scopecat").exists()
    assert registration[0][1] == Path(sys.executable).absolute()
    assert registration[0][2]["static_dir"] == tmp_path / "gui"


def test_connect_preserves_manifest_binding_and_data(tmp_path, registration):
    project = tmp_path / "lab"
    project.mkdir()
    manifest = project / "scopecat.toml"
    manifest.write_text('[lab]\nbootstrap = "lab:bootstrap"\n')
    evidence = project / ".scopecat" / "evidence"
    evidence.parent.mkdir()
    evidence.write_text("science")
    first_run.setup(
        tmp_path / "home", first_run.SetupRequest(mode="connect", project=str(project))
    )
    assert manifest.read_text() == '[lab]\nbootstrap = "lab:bootstrap"\n'
    assert evidence.read_text() == "science"
    assert not (project / RUNTIME_BINDING_NAME).exists()
    with pytest.raises(ValueError, match="已有运行位置或数据"):
        first_run.setup(
            tmp_path / "home",
            first_run.SetupRequest(
                mode="connect", project=str(project), data_root=str(tmp_path / "new")
            ),
        )
    assert len(registration) == 1


def test_existing_binding_can_be_confirmed_but_not_changed(tmp_path, registration):
    project = tmp_path / "lab"
    project.mkdir()
    (project / "scopecat.toml").write_text("[lab]\n")
    binding = project / RUNTIME_BINDING_NAME
    binding.write_text('[runtime]\ndata_root="../data"\ndeployment_root="../runtime"\n')
    before = binding.read_bytes()
    first_run.setup(
        tmp_path / "home",
        first_run.SetupRequest(
            mode="connect", project=str(project), data_root=str(tmp_path / "data")
        ),
    )
    assert binding.read_bytes() == before
    with pytest.raises(ValueError, match="已有运行位置或数据"):
        first_run.setup(
            tmp_path / "home",
            first_run.SetupRequest(
                mode="connect", project=str(project), data_root=str(tmp_path / "other")
            ),
        )


def test_broken_local_environment_and_nested_selection_fail_before_changes(
    tmp_path, registration
):
    project = tmp_path / "lab"
    project.mkdir()
    (project / "scopecat.toml").write_text("[lab]\n")
    environment = project / ".venv"
    environment.mkdir()
    with pytest.raises(ValueError, match=r"\.venv 不完整"):
        first_run.setup(
            tmp_path / "home",
            first_run.SetupRequest(mode="connect", project=str(project)),
        )
    with pytest.raises(ValueError, match="直接包含"):
        first_run.setup(
            tmp_path / "home",
            first_run.SetupRequest(mode="connect", project=str(environment)),
        )
    assert registration == []


def test_registration_failure_retains_created_project_for_explicit_reconnect(
    tmp_path, registration, monkeypatch
):
    def fail(*args, **kwargs):
        raise ValueError("missing dependency")

    monkeypatch.setattr(first_run.Services, "register", fail)
    project = tmp_path / "lab"
    with pytest.raises(ValueError, match="尚未启动实验服务"):
        first_run.setup(
            tmp_path / "home",
            first_run.SetupRequest(mode="create", project=str(project)),
        )
    assert (project / "scopecat.toml").is_file()
    assert load_runtime_binding(project).data_root == project / ".scopecat"
    with pytest.raises(ValueError, match="已有目录"):
        first_run.setup(
            tmp_path / "home",
            first_run.SetupRequest(mode="create", project=str(project)),
        )


def test_missing_gui_and_relative_paths_fail_before_creation(
    tmp_path, registration, monkeypatch
):
    with pytest.raises(ValidationError, match="绝对目录"):
        first_run.SetupRequest(mode="create", project="relative")

    def missing(**kwargs):
        raise ValueError("GUI unavailable")

    monkeypatch.setattr(first_run, "select_static_dir", missing)
    project = tmp_path / "lab"
    with pytest.raises(ValueError, match="GUI unavailable"):
        first_run.setup(
            tmp_path / "home",
            first_run.SetupRequest(mode="create", project=str(project)),
        )
    assert not project.exists()


def test_local_environment_is_selected_without_resolving_interpreter_symlink(tmp_path):
    python = (
        tmp_path
        / ".venv"
        / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    )
    python.parent.mkdir(parents=True)
    python.touch()
    assert first_run.choose_python(tmp_path) == python


@pytest.mark.parametrize("location", ["occupied", "ancestor", "source-child"])
def test_invalid_data_destination_does_not_create_project(
    tmp_path, registration, location
):
    project = tmp_path / "lab"
    data = {
        "occupied": tmp_path / "occupied",
        "ancestor": tmp_path,
        "source-child": project / "src",
    }[location]
    if location == "occupied":
        data.mkdir()
        (data / "retained").write_text("scientific evidence")
    with pytest.raises(ValueError, match="数据目录"):
        first_run.setup(
            tmp_path / "home",
            first_run.SetupRequest(
                mode="create", project=str(project), data_root=str(data)
            ),
        )
    assert not project.exists()
    assert registration == []


def test_missing_declared_dependency_fails_before_startup(tmp_path):
    project = tmp_path / "lab"
    project.mkdir()
    manifest = project / "scopecat.toml"
    manifest.write_text(
        '[lab]\nbootstrap="must_not_import:bootstrap"\n'
        '[authors]\ndependencies=["scopecat-missing-first-run-fixture==1"]\n'
    )
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>public workbench</html>")
    with pytest.raises(ValueError, match="dependency is not installed"):
        first_run.setup(
            tmp_path / "home",
            first_run.SetupRequest(mode="connect", project=str(project)),
            static_dir=gui,
        )
    assert list(project.iterdir()) == [manifest]
    assert first_run.Services(tmp_path / "home").list() == []


def test_connect_settings_preserves_data_and_updates_sidecar_atomically(
    tmp_path, registration
):
    project = tmp_path / "lab"
    project.mkdir()
    (project / "scopecat.toml").write_text("[lab]\n")
    sidecar = project / RUNTIME_BINDING_NAME
    sidecar.write_text('[runtime]\ndata_root="../data"\ndeployment_root="../runtime"\n')
    data = tmp_path / "data"
    data.mkdir()
    evidence = data / "evidence"
    evidence.write_text("retained")
    settings = tmp_path / "lab.json"
    settings.write_text('{"initial_configuration":"physical"}')
    first_run.setup(
        tmp_path / "home",
        first_run.SetupRequest(
            mode="connect", project=str(project), settings_file=str(settings)
        ),
    )
    binding = load_runtime_binding(project)
    assert binding.data_root == data
    assert binding.deployment_root == tmp_path / "runtime"
    assert binding.settings_file == settings
    assert evidence.read_text() == "retained"
    assert not list(project.glob(".runtime-*"))
    before = sidecar.read_bytes()
    first_run.setup(
        tmp_path / "home", first_run.SetupRequest(mode="connect", project=str(project))
    )
    assert sidecar.read_bytes() == before


def test_settings_change_requires_stopped_daemon(tmp_path, registration, monkeypatch):
    from types import SimpleNamespace

    project = tmp_path / "lab"
    project.mkdir()
    (project / "scopecat.toml").write_text("[lab]\n")
    settings = tmp_path / "lab.json"
    settings.write_text("{}")
    monkeypatch.setattr(
        first_run, "inspect_daemon", lambda _: SimpleNamespace(state="running")
    )
    with pytest.raises(ValueError, match="停止实验服务"):
        first_run.setup(
            tmp_path / "home",
            first_run.SetupRequest(
                mode="connect", project=str(project), settings_file=str(settings)
            ),
        )
    assert not (project / RUNTIME_BINDING_NAME).exists()
    assert registration == []


def test_invalid_settings_fail_before_project_creation(tmp_path, registration):
    settings = tmp_path / "lab.json"
    settings.write_text("[]")
    project = tmp_path / "lab"
    with pytest.raises(ValueError, match="JSON object"):
        first_run.setup(
            tmp_path / "home",
            first_run.SetupRequest(
                mode="create", project=str(project), settings_file=str(settings)
            ),
        )
    assert not project.exists()
    assert registration == []


def test_settings_change_cannot_race_daemon_owner(tmp_path, registration):
    from filelock import FileLock

    project = tmp_path / "lab"
    project.mkdir()
    (project / "scopecat.toml").write_text("[lab]\n")
    state = project / ".scopecat"
    state.mkdir()
    settings = tmp_path / "lab.json"
    settings.write_text("{}")
    with (
        FileLock(state / "deployment.lock", timeout=0),
        pytest.raises(ValueError, match="停止实验服务"),
    ):
        first_run.setup(
            tmp_path / "home",
            first_run.SetupRequest(
                mode="connect", project=str(project), settings_file=str(settings)
            ),
        )
    assert not (project / RUNTIME_BINDING_NAME).exists()
    assert registration == []


def test_delivery_preparation_uses_final_project_and_matching_gui(
    tmp_path, monkeypatch, registration
):
    from types import SimpleNamespace

    project = tmp_path / "experiment"
    delivery = tmp_path / "delivery"
    retained_gui = tmp_path / "retained/gui"
    interpreter = project / ".venv/bin/python"
    checked = []
    monkeypatch.setattr(first_run, "verify_bundle", checked.append)

    def prepare(root, selected, home):
        assert root == project and root.is_dir()
        assert (root / "scopecat.toml").is_file()
        assert selected == delivery
        assert home == tmp_path / "home"
        return SimpleNamespace(python=interpreter, gui=retained_gui)

    monkeypatch.setattr(first_run, "prepare_environment", prepare)
    first_run.setup(
        tmp_path / "home",
        first_run.SetupRequest(
            mode="create", project=str(project), environment_bundle=str(delivery)
        ),
    )
    assert checked == [delivery]
    assert registration[0][1] == interpreter
    assert registration[0][2]["static_dir"] == retained_gui


def test_invalid_delivery_does_not_publish_project(tmp_path, monkeypatch, registration):
    def invalid(_):
        raise ValueError("交付不匹配")

    monkeypatch.setattr(first_run, "verify_bundle", invalid)
    project = tmp_path / "experiment"
    with pytest.raises(ValueError, match="交付不匹配"):
        first_run.setup(
            tmp_path / "home",
            first_run.SetupRequest(
                mode="create",
                project=str(project),
                environment_bundle=str(tmp_path / "delivery"),
            ),
        )
    assert not project.exists()
    assert not registration


def test_connect_rejects_author_only_before_writing_binding(tmp_path, registration):
    project = tmp_path / "authors"
    project.mkdir()
    manifest = project / "scopecat.toml"
    manifest.write_text('[authors]\nmodules=["experiments"]\n')
    data = tmp_path / "new-data"
    with pytest.raises(ValueError, match="这是作者代码目录"):
        first_run.setup(
            tmp_path / "home",
            first_run.SetupRequest(
                mode="connect", project=str(project), data_root=str(data)
            ),
        )
    assert tuple(project.iterdir()) == (manifest,)
    assert not data.exists()
    assert not (tmp_path / "home").exists()

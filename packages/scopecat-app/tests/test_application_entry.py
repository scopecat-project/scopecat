"""Direct entry selects one application home and never an author-owned service."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scopecat_app import application
from scopecat_app.application_runtime import ApplicationRuntime


def test_select_existing_execution_environment_does_not_prepare_or_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    selected: list[tuple[Path, Path]] = []

    def record_selection(
        _self: ApplicationRuntime, workspace: Path, python: Path
    ) -> None:
        selected.append((workspace, python))

    monkeypatch.setattr(
        ApplicationRuntime,
        "select_source_environment",
        record_selection,
    )

    def unexpected_start(_runtime: ApplicationRuntime):
        return pytest.fail("must not start")

    monkeypatch.setattr(ApplicationRuntime, "start", unexpected_start)

    def unexpected_prepare(*_args: object):
        return pytest.fail("must not install dependencies")

    monkeypatch.setattr(
        "scopecat_app.author_environment.prepare_execution_environment",
        unexpected_prepare,
    )
    source, python = tmp_path / "authors", tmp_path / "user-env/python"
    application.main(
        [
            "--home",
            str(tmp_path / "home"),
            "--action",
            "select-source-environment",
            "--workspace",
            str(source),
            "--python",
            str(python),
        ]
    )
    assert selected == [(source, python)]


def test_select_environment_requires_explicit_interpreter(tmp_path: Path):
    with pytest.raises(SystemExit) as failure:
        application.main(
            [
                "--home",
                str(tmp_path / "home"),
                "--action",
                "select-source-environment",
                "--workspace",
                str(tmp_path / "authors"),
            ]
        )
    assert failure.value.code == 2
    assert not (tmp_path / "home").exists()


def test_status_before_install_has_no_side_effects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    home = tmp_path / "application"
    application.main(["--home", str(home)])
    assert json.loads(capsys.readouterr().out)["state"] == "not-installed"
    assert not home.exists()


@pytest.mark.parametrize(
    "action, no_browser, opens",
    [
        ("start", False, False),
        ("open", True, False),
        ("open", False, True),
    ],
)
def test_only_explicit_open_launches_browser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    no_browser: bool,
    opens: bool,
):
    started: list[Path] = []
    opened: list[str] = []

    def start(runtime: ApplicationRuntime):
        started.append(runtime.home)
        return SimpleNamespace(base_url="http://127.0.0.1:1234")

    monkeypatch.setattr(ApplicationRuntime, "start", start)
    monkeypatch.setattr(application.webbrowser, "open", opened.append)
    application.main(
        [
            "--home",
            str(tmp_path),
            "--action",
            action,
            *(["--no-browser"] if no_browser else []),
        ]
    )
    assert started == [tmp_path]
    assert opened == (["http://127.0.0.1:1234"] if opens else [])


def test_unbound_source_does_not_start_or_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "author"
    source.mkdir()
    (source / "scopecat.toml").write_text("[authors]\ndependencies=[]\n")
    owner = tmp_path / "home/runtime"
    owner.mkdir(parents=True)
    (owner / "scopecat.toml").write_text("[lab]\n")

    def unexpected_start(_runtime: ApplicationRuntime):
        return pytest.fail("must not start")

    monkeypatch.setattr(ApplicationRuntime, "start", unexpected_start)

    def unexpected_open(_url: str):
        return pytest.fail("must not open")

    monkeypatch.setattr(application.webbrowser, "open", unexpected_open)
    with pytest.raises(SystemExit) as failure:
        application.main(
            [
                "--home",
                str(owner.parent),
                "--action",
                "open",
                "--workspace",
                str(source),
            ]
        )
    assert failure.value.code == 2


def test_failed_start_retains_windowless_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def fail(_runtime: ApplicationRuntime):
        raise ValueError("Please stop the recorded owner")

    monkeypatch.setattr(ApplicationRuntime, "start", fail)

    def unexpected_open(_url: str):
        return pytest.fail("must not open")

    monkeypatch.setattr(application.webbrowser, "open", unexpected_open)
    with pytest.raises(SystemExit) as failure:
        application.main(["--home", str(tmp_path), "--action", "open"])
    assert failure.value.code == 2


@pytest.mark.parametrize("same_interpreter", [True, False])
def test_update_resources_follow_actual_selected_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_interpreter: bool
):
    import sys
    from pathlib import Path

    def model_dump_json_stub(**_kwargs: object) -> str:
        return "{}"

    selected = SimpleNamespace(
        python=Path(sys.executable).absolute()
        if same_interpreter
        else tmp_path / "other/python",
        delivery_root=tmp_path / "payload",
        model_dump_json=model_dump_json_stub,
    )
    calls: list[tuple[Path, Path | None]] = []

    def configure_stub(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return selected

    monkeypatch.setattr(ApplicationRuntime, "configure", configure_stub)

    def qualify(
        _runtime: ApplicationRuntime,
        python: Path,
        gui: Path,
        *,
        delivery_root: Path | None,
    ):
        calls.append((python, delivery_root))
        return selected

    monkeypatch.setattr(ApplicationRuntime, "qualify", qualify)

    def select_stub(*_args: object) -> None:
        return None

    monkeypatch.setattr(ApplicationRuntime, "select", select_stub)
    application.main(["--home", str(tmp_path), "--action", "update"])
    assert calls == [
        (Path(sys.executable), selected.delivery_root if same_interpreter else None)
    ]

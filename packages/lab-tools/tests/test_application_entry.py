"""Direct entry selects one application home and never an author-owned service."""

import json
from types import SimpleNamespace

import pytest

from lab_tools import application
from lab_tools.application_runtime import ApplicationRuntime


def test_select_existing_execution_environment_does_not_prepare_or_start(
    tmp_path, monkeypatch
):
    selected = []
    monkeypatch.setattr(
        ApplicationRuntime,
        "select_source_environment",
        lambda _self, workspace, python: selected.append((workspace, python)),
    )
    monkeypatch.setattr(
        ApplicationRuntime, "start", lambda _: pytest.fail("must not start")
    )
    monkeypatch.setattr(
        "lab_tools.author_environment.prepare_execution_environment",
        lambda *_args: pytest.fail("must not install dependencies"),
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


def test_select_environment_requires_explicit_interpreter(tmp_path):
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


def test_status_before_install_has_no_side_effects(tmp_path, capsys):
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
    tmp_path, monkeypatch, action, no_browser, opens
):
    started = []
    opened = []

    def start(runtime):
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


def test_unbound_source_does_not_start_or_open(tmp_path, monkeypatch):
    source = tmp_path / "author"
    source.mkdir()
    (source / "scopecat.toml").write_text("[authors]\ndependencies=[]\n")
    owner = tmp_path / "home/runtime"
    owner.mkdir(parents=True)
    (owner / "scopecat.toml").write_text("[lab]\n")
    monkeypatch.setattr(
        ApplicationRuntime, "start", lambda _: pytest.fail("must not start")
    )
    monkeypatch.setattr(
        application.webbrowser, "open", lambda _: pytest.fail("must not open")
    )
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


def test_failed_start_retains_windowless_recovery(tmp_path, monkeypatch):
    def fail(_runtime):
        raise ValueError("Please stop the recorded owner")

    monkeypatch.setattr(ApplicationRuntime, "start", fail)
    monkeypatch.setattr(
        application.webbrowser, "open", lambda _: pytest.fail("must not open")
    )
    with pytest.raises(SystemExit) as failure:
        application.main(["--home", str(tmp_path), "--action", "open"])
    assert failure.value.code == 2


@pytest.mark.parametrize("same_interpreter", [True, False])
def test_update_resources_follow_actual_selected_interpreter(
    tmp_path, monkeypatch, same_interpreter
):
    import sys
    from pathlib import Path

    selected = SimpleNamespace(
        python=Path(sys.executable).absolute()
        if same_interpreter
        else tmp_path / "other/python",
        delivery_root=tmp_path / "payload",
        model_dump_json=lambda **_kwargs: "{}",
    )
    calls = []
    monkeypatch.setattr(
        ApplicationRuntime, "configure", lambda *_args, **_kwargs: selected
    )

    def qualify(_runtime, python, gui, **kwargs):
        calls.append((python, kwargs["delivery_root"]))
        return selected

    monkeypatch.setattr(ApplicationRuntime, "qualify", qualify)
    monkeypatch.setattr(ApplicationRuntime, "select", lambda *_args: None)
    application.main(["--home", str(tmp_path), "--action", "update"])
    assert calls == [
        (Path(sys.executable), selected.delivery_root if same_interpreter else None)
    ]

"""Direct entry selects one application home and never an author-owned service."""

import json
from types import SimpleNamespace

import pytest

from lab_tools import application
from lab_tools.application_runtime import ApplicationRuntime


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

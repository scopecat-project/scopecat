"""Daily entry selects an explicit deployment without bypassing startup checks."""

import secrets
import sqlite3
import sys
from contextlib import closing
from types import SimpleNamespace

import pytest

from lab_tools import application, services
from lab_tools.host_models import Operation


@pytest.fixture
def entry(tmp_path, monkeypatch):
    home = tmp_path / "home"
    store = services.Services(home)
    opened = []
    submitted = []
    monkeypatch.setattr(
        services,
        "_run",
        lambda python, request: {
            "root": request["root"],
            "static_dir": str(tmp_path / "gui"),
            "settings_identity": None,
            "environment": {"python": python},
        },
    )
    monkeypatch.setattr(application.webbrowser, "open", opened.append)
    client = SimpleNamespace(
        record=SimpleNamespace(
            url="http://127.0.0.1:9876", token=secrets.token_urlsafe(32)
        ),
        submit=lambda command: (
            submitted.append(command) or Operation(command=command, status="succeeded")
        ),
        wait=lambda operation: operation,
        state=lambda: SimpleNamespace(
            services=[
                services.ServiceView(
                    service=item, state="running", url="http://127.0.0.1:1234"
                )
                for item in store.list()
            ],
            model_dump_json=lambda **_kwargs: "state-only",
        ),
    )
    monkeypatch.setattr(application, "ensure_host", lambda *_args: client)

    def register(name):
        return store.register(
            tmp_path / name, application.Path(sys.executable), name=name
        )

    def invoke(*args):
        application.main(["--home", str(home), *args])

    return SimpleNamespace(
        store=store,
        opened=opened,
        submitted=submitted,
        client=client,
        register=register,
        invoke=invoke,
        tmp_path=tmp_path,
    )


def test_explicit_then_remembered_selection_uses_durable_start(entry):
    other = entry.register("other")
    entry.invoke(str(entry.tmp_path / "main"))
    selected = entry.store.preferred()
    assert selected is not None and selected.id != other.id
    assert entry.submitted[-1].service == selected.id
    assert entry.submitted[-1].action == "service_start"
    assert entry.opened == ["http://127.0.0.1:1234"]
    entry.invoke()
    assert entry.submitted[-1].service == selected.id


def test_sole_selection_and_management_override(entry):
    selected = entry.register("main")
    entry.invoke("--manage")
    assert entry.submitted == []
    assert "#token=" in entry.opened[-1]
    entry.invoke()
    assert entry.submitted[-1].service == selected.id
    assert entry.store.preferred() == selected


def test_unselected_multiple_or_removed_primary_never_redirects(entry):
    first = entry.register("first")
    second = entry.register("second")
    entry.invoke()
    assert entry.submitted == []
    entry.store.remember(first.id)
    with closing(sqlite3.connect(entry.store.database)) as db, db:
        db.execute("DELETE FROM services WHERE id=?", (first.id,))
    assert entry.store.list() == [second]
    assert entry.store.preferred() is None
    entry.invoke()
    assert entry.submitted == []
    assert "#token=" in entry.opened[-1]


def test_no_browser_keeps_state_only_and_does_not_choose(entry, capsys):
    entry.invoke(str(entry.tmp_path / "main"), "--no-browser")
    entry.register("other")
    assert entry.store.preferred() is None
    assert entry.opened == entry.submitted == []
    assert "state-only" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["operation", "fresh-state"])
def test_failed_start_keeps_previous_choice_and_opens_manager(entry, failure):
    first = entry.register("first")
    entry.store.remember(first.id)
    if failure == "operation":

        def wait(operation):
            raise ValueError(f"startup failed; operation {operation.command.id}")

        entry.client.wait = wait
    else:
        entry.client.state = lambda: SimpleNamespace(services=[])
    with pytest.raises(SystemExit) as error:
        entry.invoke(str(entry.tmp_path / "second"))
    assert error.value.code == 2
    assert entry.store.preferred() == first
    assert len(entry.opened) == 1 and "#token=" in entry.opened[0]

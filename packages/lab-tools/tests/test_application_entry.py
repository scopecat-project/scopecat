"""Daily entry selects an explicit deployment without bypassing startup checks."""

import json
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
            "adapter_identity": None,
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


def test_explicit_start_remembers_selection_without_browser(entry, monkeypatch):
    started = []
    monkeypatch.setattr(
        services.Services, "start", lambda _self, identity: started.append(identity)
    )
    other = entry.register("other")
    entry.invoke("--action", "start", str(entry.tmp_path / "main"))
    selected = entry.store.preferred()
    assert selected is not None and selected.id != other.id
    assert started == [selected.id]
    assert entry.opened == []
    entry.invoke("--action", "start")
    assert started == [selected.id, selected.id]


def test_sole_selection_and_management_override(entry):
    selected = entry.register("main")
    entry.invoke("--action", "open", "--manage")
    assert entry.submitted == []
    assert "#token=" in entry.opened[-1]
    entry.invoke("--action", "open")
    assert f"service={selected.id}" in entry.opened[-1]
    assert not entry.submitted
    assert entry.store.preferred() == selected


def test_unselected_multiple_or_removed_primary_never_redirects(entry):
    first = entry.register("first")
    second = entry.register("second")
    entry.invoke("--action", "open")
    assert entry.submitted == []
    entry.store.remember(first.id)
    with closing(sqlite3.connect(entry.store.database)) as db, db:
        db.execute("DELETE FROM services WHERE id=?", (first.id,))
    assert entry.store.list() == [second]
    assert entry.store.preferred() is None
    entry.invoke("--action", "open")
    assert entry.submitted == []
    assert "#token=" in entry.opened[-1]


def test_no_browser_keeps_state_only_and_does_not_choose(entry, capsys):
    entry.invoke("--action", "open", str(entry.tmp_path / "main"), "--no-browser")
    entry.register("other")
    assert entry.store.preferred() is None
    assert entry.opened == entry.submitted == []
    assert "state-only" in capsys.readouterr().out


def test_failed_start_keeps_previous_choice_without_opening_browser(entry, monkeypatch):
    first = entry.register("first")
    entry.store.remember(first.id)

    def fail(self, identity):
        raise ValueError("startup failed")

    monkeypatch.setattr(services.Services, "start", fail)
    with pytest.raises(SystemExit) as error:
        entry.invoke("--action", "start", str(entry.tmp_path / "second"))
    assert error.value.code == 2
    assert entry.store.preferred() == first
    assert entry.opened == []


def test_default_status_does_not_start_host(entry, monkeypatch, capsys):
    def unexpected(*args):
        pytest.fail("status must not start a host")

    monkeypatch.setattr(application, "ensure_host", unexpected)
    entry.invoke()
    assert json.loads(capsys.readouterr().out) == {"host": None, "services": []}
    assert not entry.opened


def bind_source(entry, owner, *, python=None):
    from scopecat.author_workspaces import LocalAuthorWorkspace, LocalAuthorWorkspaces

    root = entry.tmp_path / "作者代码"
    root.mkdir()
    owner_root = application.Path(owner.root)
    owner_root.mkdir(exist_ok=True)
    for location in (root, owner_root):
        (location / "scopecat.toml").write_text("[lab]\n")
    data = owner_root / ".scopecat"
    data.mkdir()
    (root / "scopecat.runtime.toml").write_text(
        "[runtime]\ndata_root = "
        + json.dumps(str(data))
        + "\ndeployment_root = "
        + json.dumps(str(data))
        + "\n"
    )
    (data / "author-workspaces.json").write_text(
        LocalAuthorWorkspaces(
            service_root=owner_root,
            items=(
                LocalAuthorWorkspace(
                    id="source-b",
                    name="Author B",
                    root=root,
                    python=python or application.Path(owner.python),
                ),
            ),
        ).model_dump_json()
    )
    return root


def test_bound_source_opens_existing_owner_without_registration(entry, monkeypatch):
    owner = entry.register("lab")
    root = bind_source(entry, owner)

    def unexpected(*args, **kwargs):
        pytest.fail("Opening bound code must not register or probe another service")

    monkeypatch.setattr(services.Services, "register", unexpected)
    entry.invoke("--action", "open", "--workspace", str(root))
    assert entry.store.list() == [owner]
    assert not entry.submitted
    assert f"service={owner.id}&workspace=source-b" in entry.opened[-1]
    assert entry.store.preferred() == owner


@pytest.mark.parametrize("failure", ["unbound", "missing-owner", "python"])
def test_source_binding_failure_does_not_start_or_register(entry, failure):
    owner = entry.register("lab")
    root = bind_source(
        entry,
        owner,
        python=entry.tmp_path / "other/python" if failure == "python" else None,
    )
    if failure == "unbound":
        (root / "scopecat.runtime.toml").unlink()
    if failure == "missing-owner":
        with closing(sqlite3.connect(entry.store.database)) as db, db:
            db.execute("DELETE FROM services")
    before = entry.store.list()
    with pytest.raises(SystemExit) as error:
        entry.invoke("--action", "open", "--workspace", str(root))
    assert error.value.code == 2
    assert entry.store.list() == before
    assert not entry.opened and not entry.submitted


@pytest.mark.parametrize("option", ["--python", "--name", "--static-dir"])
def test_source_entry_cannot_override_laboratory_binding(entry, option):
    with pytest.raises(SystemExit) as error:
        entry.invoke("--workspace", str(entry.tmp_path), option, "override")
    assert error.value.code == 2
    assert entry.store.list() == []
    assert not entry.opened and not entry.submitted

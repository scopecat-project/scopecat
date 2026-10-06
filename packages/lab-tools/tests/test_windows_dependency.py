"""Patched-source sequencing contracts, not a substitute for native WebView2."""

import ast
import base64
import csv
import hashlib
import importlib.metadata
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace

import pytest

from lab_tools import windows_dependency
from lab_tools.dependency_wheel import write_wheel


@pytest.fixture(scope="module")
def source_pair():
    source = Path(
        importlib.metadata.distribution("pywebview").locate_file(
            "webview/platforms/edgechromium.py"
        )
    ).read_bytes()
    return source, windows_dependency.patch_windows(source)


class Task:
    def __init__(self):
        self.IsCanceled = False
        self.IsFaulted = False
        self.Exception = None
        self.completed = False
        self.continuations = []

    def ContinueWith(self, callback, scheduler):
        if self.completed:
            scheduler.append((callback, self))
        else:
            self.continuations.append((callback, scheduler))

    def complete(self, failure=None):
        self.IsCanceled = failure == "cancelled"
        self.IsFaulted = failure == "faulted"
        self.Exception = failure
        self.completed = True
        for callback, scheduler in self.continuations:
            scheduler.append((callback, self))
        self.continuations.clear()


class Action:
    def __class_getitem__(cls, item):
        return lambda callback: callback


def drain(view):
    while view.syncContextTaskScheduler:
        callback, task = view.syncContextTaskScheduler.pop(0)
        callback(task)


@pytest.fixture
def browser_class(source_pair):
    tree = ast.parse(source_pair[1])
    browser = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    names = {
        "_initialize_private_store",
        "_on_private_store_ready",
        "_load_initial_content",
        "load_url",
        "load_html",
        "clear_cookies",
    }
    browser.body = [
        node
        for node in browser.body
        if isinstance(node, ast.Assign)
        or (isinstance(node, ast.FunctionDef) and node.name in names)
    ]
    scope = {
        "Lock": Lock,
        "os": __import__("os"),
        "Task": Task,
        "Action": Action,
        "CoreWebView2BrowsingDataKinds": SimpleNamespace(Cookies="cookies-only"),
        "logger": logging.getLogger(__name__),
        "Uri": str,
        "DEFAULT_HTML": "default",
        "_state": {"debug": False},
        "webview_settings": {},
    }
    exec(  # noqa: S102 - execute only methods from the hash-checked vendor patch
        compile(ast.Module(body=[browser], type_ignores=[]), "patched-edge", "exec"),
        scope,
    )
    return scope["EdgeChrome"]


class Profile:
    ProfileName = "Default"

    def __init__(self, *, error=False):
        self.calls = []
        self.task = Task()
        self.error = error

    def ClearBrowsingDataAsync(self, kinds):
        self.calls.append(kinds)
        if self.error:
            raise RuntimeError("cannot begin clear")
        return self.task


def make_view(browser_class, profile, folder="host-a", *, private=True):
    view = browser_class()
    view._private_store_ready = not private
    view._pending_navigation = None
    view.syncContextTaskScheduler = []
    view.pywebview_window = SimpleNamespace(real_url="https://initial", html=None)
    view.webview = SimpleNamespace(
        IsDisposed=False,
        Source=None,
        CoreWebView2=SimpleNamespace(
            Profile=profile,
            Environment=SimpleNamespace(UserDataFolder=folder),
            NavigateToString=lambda value: setattr(view.webview, "Source", value),
        ),
    )
    return view


def test_pending_peers_wait_for_one_completed_clear_and_later_peer_reuses_it(
    browser_class,
):
    profile = Profile()
    views = [make_view(browser_class, profile) for _ in range(6)]
    barrier = Barrier(len(views))

    def initialize(view):
        barrier.wait()
        view._initialize_private_store()

    with ThreadPoolExecutor(max_workers=len(views)) as executor:
        list(executor.map(initialize, views))
    assert profile.calls == ["cookies-only"]
    assert all(v.webview.Source is None for v in views)
    views[0].load_url("https://superseded")
    views[0].load_html("latest html", "ignored-by-upstream")
    views[1].load_url("https://latest")
    assert all(v.webview.Source is None for v in views)
    profile.task.complete()
    assert all(v.webview.Source is None for v in views)  # UI dispatch still required
    for view in views:
        drain(view)
    assert views[0].webview.Source == "latest html"
    assert views[1].webview.Source == "https://latest"
    assert all(v.webview.Source == "https://initial" for v in views[2:])
    late = make_view(browser_class, profile)
    late._initialize_private_store()
    drain(late)
    assert late.webview.Source == "https://initial"
    assert profile.calls == ["cookies-only"]


@pytest.mark.parametrize("failure", ["faulted", "cancelled", "synchronous"])
def test_failed_initialization_never_navigates_or_retries(browser_class, failure):
    profile = Profile(error=failure == "synchronous")
    first = make_view(browser_class, profile)
    first._initialize_private_store()
    if failure != "synchronous":
        profile.task.complete(failure)
    later = make_view(browser_class, profile)
    later._initialize_private_store()
    for view in [first, later]:
        drain(view)
        view.load_url("https://must-not-load")
        view.load_html("must-not-load", "")
        assert view.webview.Source is None
        assert not view._private_store_ready
    assert profile.calls == ["cookies-only"]


def test_distinct_profile_keys_initialize_independently(browser_class):
    profiles = [Profile() for _ in range(3)]
    profiles[2].ProfileName = "Other"
    views = [
        make_view(browser_class, p, folder)
        for p, folder in zip(profiles, ["host-a", "host-b", "host-a"], strict=True)
    ]
    for view in views:
        view._initialize_private_store()
    assert all(p.calls == ["cookies-only"] for p in profiles)
    profiles[0].task.complete()
    for view in views:
        drain(view)
    assert views[0].webview.Source == "https://initial"
    assert all(v.webview.Source is None for v in views[1:])


def test_nonprivate_navigation_and_closed_pending_view(browser_class):
    profile = Profile()
    public = make_view(browser_class, profile, private=False)
    public.load_url("https://public")
    assert public.webview.Source == "https://public"
    public.load_html("public html", "")
    assert public.webview.Source == "public html"
    assert profile.calls == []
    private = make_view(browser_class, profile)
    private._initialize_private_store()
    private.webview.IsDisposed = True
    profile.task.complete()
    drain(private)
    assert private.webview.Source is None


def test_explicit_cookie_api_is_unchanged(source_pair):
    def methods(source):
        browser = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef))
        return {n.name: n for n in browser.body if isinstance(n, ast.FunctionDef)}

    before, after = map(methods, source_pair)
    for name in ["clear_cookies", "get_cookies", "clear_user_data"]:
        assert ast.dump(before[name]) == ast.dump(after[name])


def test_windows_patch_rejects_unknown_source_and_wheel(tmp_path):
    with pytest.raises(ValueError, match="differs"):
        windows_dependency.patch_windows(b"unknown backend")
    (tmp_path / "pywebview-6.2.1-py3-none-any.whl").write_bytes(b"unknown wheel")
    with pytest.raises(ValueError, match="differs"):
        windows_dependency.patch_wheel(tmp_path)


def test_shared_wheel_writer_records_every_member_reproducibly(tmp_path):
    import zipfile

    record = "example.dist-info/RECORD"
    members = {"example.py": b"content", "LICENSE": b"retained", record: b"old"}
    outputs = [tmp_path / name for name in ["a.whl", "b.whl"]]
    for output in outputs:
        write_wheel(dict(members), output, record)
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    with zipfile.ZipFile(outputs[0]) as archive:
        rows = list(csv.reader(io.StringIO(archive.read(record).decode())))
        assert {row[0] for row in rows} == set(archive.namelist())
        for name, digest, size in rows:
            if name == record:
                assert (digest, size) == ("", "")
                continue
            data = archive.read(name)
            assert int(size) == len(data)
            assert digest == "sha256=" + base64.urlsafe_b64encode(
                hashlib.sha256(data).digest()
            ).decode().rstrip("=")


@pytest.mark.parametrize("private", [True, False])
def test_debug_initial_navigation_uses_its_own_webview(browser_class, private):
    profile = Profile()
    view = make_view(browser_class, profile, private=private)
    opened = []
    view.webview.CoreWebView2.OpenDevToolsWindow = lambda: opened.append(view)
    scope = browser_class._load_initial_content.__globals__
    scope["_state"]["debug"] = True
    scope["webview_settings"]["OPEN_DEVTOOLS_IN_DEBUG"] = True
    if private:
        view._initialize_private_store()
        profile.task.complete()
        drain(view)
    else:
        view._load_initial_content()
    assert view.webview.Source == "https://initial"
    assert opened == [view]

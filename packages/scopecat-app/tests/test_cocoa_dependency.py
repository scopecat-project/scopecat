"""Source/packaging contracts only; these tests do not execute Cocoa."""

import ast
import importlib.metadata
import json
import logging
import sys
import threading
from pathlib import Path
from types import FunctionType, SimpleNamespace
from typing import cast

import pytest
from scripts.app_build import cocoa_dependency


@pytest.mark.skipif(sys.platform == "win32", reason="Darwin dependency build tool")
def test_locked_cocoa_patch_constructs_with_shared_store_and_preserves_cookie_api():
    source = Path(
        str(
            importlib.metadata.distribution("pywebview").locate_file(
                "webview/platforms/cocoa.py"
            )
        )
    ).read_bytes()
    patched = cocoa_dependency.patch_cocoa(source)
    original_tree, patched_tree = ast.parse(source), ast.parse(patched)

    def methods(tree):
        browser = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "BrowserView"
        )
        return {
            node.name: node
            for node in browser.body
            if isinstance(node, ast.FunctionDef)
        }

    originals, functions = methods(original_tree), methods(patched_tree)
    assert ast.dump(functions["clear_cookies"]) == ast.dump(originals["clear_cookies"])
    # The sole cookie API change corrects the public Objective-C selector spelling.
    for node in ast.walk(originals["get_cookies"]):
        if isinstance(node, ast.Attribute) and node.attr == "SameSitePolicy":
            node.attr = "sameSitePolicy"
    assert ast.dump(functions["get_cookies"]) == ast.dump(originals["get_cookies"])
    calls = [
        (node.lineno, node.func.attr)
        for node in ast.walk(patched_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    line = {name: number for number, name in calls}
    assert (
        line["nonPersistentDataStore"]
        < line["setWebsiteDataStore_"]
        < line["initWithFrame_configuration_"]
    )
    assert "defaultDataStore" not in line
    assert (
        sum(
            name == "removeDataOfTypes_modifiedSince_completionHandler_"
            for _, name in calls
        )
        == 1
    )
    assert (
        b"self.datastore = self.webview.configuration().websiteDataStore()" in patched
    )


def test_patch_rejects_unreviewed_source():
    with pytest.raises(ValueError, match="differs"):
        cocoa_dependency.patch_cocoa(b"another backend")


def test_wheel_rejects_unreviewed_dependency(tmp_path: Path):
    (tmp_path / "pywebview-6.2.1-py3-none-any.whl").write_bytes(b"unreviewed")
    with pytest.raises(ValueError, match="differs"):
        cocoa_dependency.patch_wheel(tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="Darwin dependency build tool")
@pytest.mark.parametrize("close_before_dispatch", [False, True])
def test_closed_window_releases_pending_javascript_without_webkit_completion(
    close_before_dispatch,
):
    """Execute the repaired method with a controllable Cocoa callback queue."""
    evaluate = cocoa_evaluate()
    queued = []
    scheduled = threading.Event()
    closed = threading.Event()
    evaluated = []

    def call_after(callback):
        queued.append(callback)
        scheduled.set()

    evaluate.__globals__["AppHelper"] = SimpleNamespace(callAfter=call_after)
    view = SimpleNamespace(
        closed=closed,
        webview=SimpleNamespace(
            evaluateJavaScript_completionHandler_=lambda *args: evaluated.append(args)
        ),
    )
    result = []
    caller = threading.Thread(
        target=lambda: result.append(evaluate(view, "1 + 1", True)), daemon=True
    )
    caller.start()
    assert scheduled.wait(2)
    try:
        if not close_before_dispatch:
            queued[0]()
            assert len(evaluated) == 1
            assert caller.is_alive()  # No fabricated completion while still open.
        closed.set()
        caller.join(2)
        assert not caller.is_alive()
        assert result == [None]
        if close_before_dispatch:
            queued[0]()
            assert not evaluated
        else:
            # A late WebKit callback must remain harmless.
            evaluated[0][1]("2", None)
    finally:
        closed.set()
        caller.join(2)


@pytest.mark.skipif(sys.platform == "win32", reason="Darwin dependency build tool")
def test_live_window_keeps_javascript_result():
    evaluate = cocoa_evaluate()
    evaluate.__globals__["AppHelper"] = SimpleNamespace(
        callAfter=lambda callback: callback()
    )
    view = SimpleNamespace(
        closed=threading.Event(),
        webview=SimpleNamespace(
            evaluateJavaScript_completionHandler_=lambda _script, callback: callback(
                '{"answer": 42}', None
            )
        ),
    )
    assert evaluate(view, "script", True) == {"answer": 42}
    assert evaluate(view, "script", False) == '{"answer": 42}'


def cocoa_evaluate() -> FunctionType:
    source = Path(
        str(
            importlib.metadata.distribution("pywebview").locate_file(
                "webview/platforms/cocoa.py"
            )
        )
    ).read_bytes()
    tree = ast.parse(cocoa_dependency.patch_cocoa(source))
    browser = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BrowserView"
    )
    method = next(
        node
        for node in browser.body
        if isinstance(node, ast.FunctionDef) and node.name == "evaluate_js"
    )
    namespace: dict[str, object] = {
        "Semaphore": threading.Semaphore,
        "json": json,
        "logger": logging.getLogger(__name__),
    }
    exec(  # noqa: S102 - execute only the hash-verified dependency method above
        compile(ast.Module(body=[method], type_ignores=[]), "<patched-cocoa>", "exec"),
        namespace,
    )
    return cast("FunctionType", namespace["evaluate_js"])

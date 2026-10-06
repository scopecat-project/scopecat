"""Source/packaging contracts only; these tests do not execute Cocoa."""

import ast
import importlib.metadata
import sys
from pathlib import Path

import pytest

from lab_tools import cocoa_dependency


@pytest.mark.skipif(sys.platform == "win32", reason="Darwin dependency build tool")
def test_locked_cocoa_patch_constructs_with_shared_store_and_preserves_cookie_api():
    source = Path(
        importlib.metadata.distribution("pywebview").locate_file(
            "webview/platforms/cocoa.py"
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


def test_wheel_rejects_unreviewed_dependency(tmp_path):
    (tmp_path / "pywebview-6.2.1-py3-none-any.whl").write_bytes(b"unreviewed")
    with pytest.raises(ValueError, match="differs"):
        cocoa_dependency.patch_wheel(tmp_path)

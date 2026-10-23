"""Regression checks for audit source identities and incomplete scanner reports."""

from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import cast

from scripts.dependency_security_report import summarize, workspace_config


class SecurityReportTests(unittest.TestCase):
    def test_overrides_follow_local_sources_not_package_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lockfile = Path(directory) / "uv.lock"
            _ = lockfile.write_text("""
[[package]]
name = "local-author"
version = "0.1.0"
source = { editable = "packages/author" }
[[package]]
name = "tutorial"
version = "0.1.0"
source = { directory = "packages/tutorial" }
[[package]]
name = "root"
source = { virtual = "." }
[[package]]
name = "scopecat-registry-dependency"
version = "1.2.3"
source = { registry = "https://pypi.org/simple" }
[[package]]
name = "local-author"
version = "2.0.0"
source = { registry = "https://pypi.org/simple" }
""")
            overrides = cast(
                "list[dict[str, object]]",
                tomllib.loads(workspace_config(lockfile))["PackageOverrides"],
            )
            assert {(p["name"], p["version"]) for p in overrides} == {
                ("local-author", "0.1.0"),
                ("tutorial", "0.1.0"),
            }
            assert all(p["ecosystem"] == "PyPI" and p["ignore"] for p in overrides)

    def test_exact_registry_collision_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lockfile = Path(directory) / "uv.lock"
            _ = lockfile.write_text("""
[[package]]
name = "Local_Author"
version = "0.1.0"
source = { editable = "packages/author" }
[[package]]
name = "local-author"
version = "0.1.0"
source = { registry = "https://pypi.org/simple" }
""")
            with self.assertRaisesRegex(ValueError, "identity collision"):
                _ = workspace_config(lockfile)

    def test_missing_inventory_is_not_a_clean_report(self) -> None:
        with self.assertRaisesRegex(ValueError, "Missing or empty"):
            _ = summarize({"results": []})
        with self.assertRaisesRegex(ValueError, "Missing or empty"):
            _ = summarize(
                {"results": [{"source": {"path": "uv.lock"}, "packages": []}]}
            )


if __name__ == "__main__":
    _ = unittest.main()

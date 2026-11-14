"""Mark and inspect an isolated trial package. Inspection never starts software."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import plistlib
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from scopecat.trial_policy import checked_path, read_policy


@dataclass(frozen=True)
class TrialBuild:
    home: Path
    locator: Path
    identity: str

    def validate(self) -> None:
        home, locator = checked_path(self.home), checked_path(self.locator)
        if home.exists():
            raise ValueError("A trial build requires a fresh, unused isolated home")
        if locator == home or not locator.is_relative_to(home):
            raise ValueError("Trial locator must be below the isolated home")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", self.identity):
            raise ValueError("Trial ID must be a short lowercase name")


def core_package(python_home: Path) -> Path:
    candidates = [python_home / "Lib/site-packages/scopecat"]
    candidates.extend(python_home.glob("lib/python*/site-packages/scopecat"))
    matches = [path for path in candidates if (path / "__init__.py").is_file()]
    if len(matches) != 1:
        raise ValueError("Cannot identify exactly one bundled core package")
    return matches[0]


def install_policy(resources: Path, trial: TrialBuild) -> str:
    trial.validate()
    raw = json.dumps(
        {
            "version": 1,
            "id": trial.identity,
            "home": str(trial.home),
            "locator": str(trial.locator),
        },
        sort_keys=True,
    ).encode()
    digest = hashlib.sha256(raw).hexdigest()
    (resources / "trial-policy.json").write_bytes(raw)
    package = core_package(resources / "python")
    relative = os.path.relpath(resources, package)
    with (package / "__init__.py").open("a", encoding="utf-8") as stream:
        stream.write(
            f"\n# SCOPECAT_TRIAL_REQUIRED {digest}\n"
            "from .trial_policy import enforce_required as _enforce_trial\n"
            f"_TRIAL_POLICY = _enforce_trial(__file__, {relative!r}, {digest!r})\n"
        )
    return digest


def resources_for(app: Path) -> Path:
    return app / ("Contents/Resources" if app.suffix == ".app" else "resources")


def refuse_automatic_verification(app: Path) -> None:
    resources = resources_for(app)
    bootstrap = resources / "bootstrap.py"
    runtime_marker = False
    with suppress(ValueError):
        runtime_marker = "_TRIAL_POLICY =" in (
            core_package(resources / "python") / "__init__.py"
        ).read_text(encoding="utf-8")
    if (
        runtime_marker
        or (resources / "trial-policy.json").exists()
        or (
            bootstrap.is_file()
            and "expected_trial_digest=" in bootstrap.read_text(encoding="utf-8")
        )
    ):
        raise ValueError(
            "Trial verification is read-only; use scripts.app_build.trial_package. "
            "Automatic startup/restart is not allowed"
        )


def inspect(app: Path) -> dict[str, str]:
    resources = checked_path(resources_for(app))
    bootstrap = checked_path(resources / "bootstrap.py").read_text(encoding="utf-8")
    bootstrap_tree = ast.parse(bootstrap)
    calls = [
        node.value
        for node in bootstrap_tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "main"
    ]
    if len(calls) != 1 or calls[0].args or len(calls[0].keywords) != 1:
        raise ValueError("Package has no required trial bootstrap marker")
    keyword = calls[0].keywords[0]
    if (
        keyword.arg != "expected_trial_digest"
        or not isinstance(keyword.value, ast.Constant)
        or not isinstance(keyword.value.value, str)
        or not re.fullmatch(r"[0-9a-f]{64}", keyword.value.value)
    ):
        raise ValueError("Package has no required trial bootstrap marker")
    digest = keyword.value.value
    package = core_package(resources / "python")
    tree = ast.parse(checked_path(package / "__init__.py").read_text(encoding="utf-8"))
    hooks = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_TRIAL_POLICY"
            for target in node.targets
        )
    ]
    if len(hooks) != 1 or not isinstance(hooks[0], ast.Call):
        raise ValueError("Trial bootstrap and runtime markers do not match")
    hook = hooks[0]
    if not (
        isinstance(hook.func, ast.Name)
        and hook.func.id == "_enforce_trial"
        and len(hook.args) == 3
        and isinstance(hook.args[0], ast.Name)
        and hook.args[0].id == "__file__"
        and isinstance(hook.args[1], ast.Constant)
        and isinstance(hook.args[1].value, str)
        and isinstance(hook.args[2], ast.Constant)
        and hook.args[2].value == digest
    ):
        raise ValueError("Trial bootstrap and runtime markers do not match")
    if Path(os.path.abspath(package / hook.args[1].value)) != resources:  # noqa: PTH100 - retain symlink evidence
        raise ValueError("Trial runtime marker points outside this package")
    imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "trial_policy"
        and any(
            item.name == "enforce_required" and item.asname == "_enforce_trial"
            for item in node.names
        )
    ]
    if len(imports) != 1:
        raise ValueError("Trial runtime has no required policy import")
    policy = read_policy(resources, digest)
    if app.suffix == ".app":
        with checked_path(app / "Contents/Info.plist").open("rb") as stream:
            info = cast("dict[str, object]", plistlib.load(stream))
        if (
            info.get("CFBundleIdentifier") != f"org.scopecat.trial.{policy.identity}"
            or info.get("CFBundleName") != f"Scopecat Trial {policy.identity}"
            or info.get("CFBundleDisplayName") != f"Scopecat Trial {policy.identity}"
        ):
            raise ValueError("Trial bundle identity does not match its policy")
    return {
        "id": policy.identity,
        "home": str(policy.home),
        "state": str(policy.state),
        "locator": str(policy.locator),
        "policy_sha256": digest,
        "mode": "read-only; no application was started",
        "signature": "not checked; verify the native signature before launching",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect(cast("Path", args.app)), indent=2))


if __name__ == "__main__":
    main()

"""Fail-closed boundaries for explicitly marked, local native trial packages.

This is an accidental-entry guard, not a sandbox for arbitrary Python code.
Ordinary installations do not carry the required policy hook.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast


@dataclass(frozen=True)
class TrialPolicy:
    identity: str
    home: Path
    locator: Path
    resources: Path

    @property
    def state(self) -> Path:
        return self.home / "data"


def checked_path(path: Path) -> Path:
    """Reject aliases before resolve can erase symlink/junction evidence."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Trial paths must be absolute, without parent traversal")
    for part in (*reversed(path.parents), path):
        if part.is_symlink() or part.is_junction():
            raise ValueError(f"Trial path contains a symlink or junction: {part}")
    return path.resolve()


def validate_tree(root: Path) -> None:
    """Reject existing and dangling aliases, including files about to be opened.

    This is rechecked at entry boundaries, not an OS-level TOCTOU sandbox.
    """
    checked_path(root)

    def failed_scan(error: OSError) -> None:
        raise error

    if root.exists():
        for directory, directories, filenames in root.walk(
            follow_symlinks=False, on_error=failed_scan
        ):
            for name in (*directories, *filenames):
                checked_path(directory / name)


def read_policy(resources: Path, expected_digest: str) -> TrialPolicy:
    resources = checked_path(resources)
    policy_path = resources / "trial-policy.json"
    checked_path(policy_path)
    raw = policy_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_digest:
        raise ValueError("Trial policy is missing or does not match this package")
    value = cast("object", json.loads(raw))
    if not isinstance(value, dict):
        raise ValueError("Invalid trial policy")
    document = cast("dict[str, object]", value)
    if set(document) != {
        "version",
        "id",
        "home",
        "locator",
    }:
        raise ValueError("Invalid trial policy")
    identity, home, locator = document["id"], document["home"], document["locator"]
    if (
        type(document["version"]) is not int
        or document["version"] != 1
        or not isinstance(identity, str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", identity)
    ):
        raise ValueError("Invalid trial identity")
    if not isinstance(home, str) or not isinstance(locator, str):
        raise ValueError("Invalid trial paths")
    root, location = checked_path(Path(home)), checked_path(Path(locator))
    if location == root or not location.is_relative_to(root):
        raise ValueError("Trial locator must be inside the isolated home")
    validate_tree(root)
    return TrialPolicy(identity, root, location, resources)


def enforce_required(
    package_file: str, relative_resources: str, digest: str
) -> TrialPolicy:
    # The build appends this call to the bundled core package before signing.
    # Removing the JSON cannot turn a marked trial back into a normal install.
    checked_path(Path(package_file))
    resources = Path(os.path.abspath(Path(package_file).parent / relative_resources))  # noqa: PTH100 - normalize .. without erasing symlink evidence
    policy = read_policy(resources, digest)
    validate_environment(policy)
    return policy


def current_policy() -> TrialPolicy | None:
    value: object = getattr(sys.modules["scopecat"], "_TRIAL_POLICY", None)
    return value if isinstance(value, TrialPolicy) else None


def validate_environment(policy: TrialPolicy) -> None:
    value = os.environ.get("SCOPECAT_LOCATOR_HOME")
    if not value or checked_path(Path(value)) != policy.locator:
        raise ValueError(
            "Trial requires its exact SCOPECAT_LOCATOR_HOME; no default is allowed"
        )
    validate_tree(policy.home)
    checked_path(policy.locator)


def validate_home(home: Path) -> None:
    if (policy := current_policy()) is not None:
        validate_environment(policy)
        if checked_path(home) != policy.state:
            raise ValueError("Trial application data home does not match this package")


def validate_arguments(arguments: Sequence[str], *, native: bool = False) -> None:
    policy = current_policy()
    if policy is None:
        return
    validate_environment(policy)
    for option in (
        "--home",
        "--payload",
        "--entry",
        "--check-result",
        "--trial-window-probe",
    ):
        occurrences = sum(
            value == option or value.startswith(option + "=") for value in arguments
        )
        if occurrences > 1:
            raise ValueError(f"Duplicate trial argument: {option}")
    homes = [
        index
        for index, value in enumerate(arguments)
        if value == "--home" or value.startswith("--home=")
    ]
    if len(homes) != 1:
        raise ValueError("Trial requires an explicit --home; no default is allowed")
    index = homes[0]
    value = (
        arguments[index].partition("=")[2]
        if "=" in arguments[index]
        else arguments[index + 1]
        if index + 1 < len(arguments)
        else ""
    )
    expected = policy.home if native else policy.state
    if not value or checked_path(Path(value)) != expected:
        raise ValueError("Trial --home does not match this package")


def refuse_trial_entry(name: str) -> None:
    if current_policy() is not None:
        raise ValueError(f"{name} is not supported by this isolated trial package")

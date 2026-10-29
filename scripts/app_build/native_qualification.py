"""Describe the current native build policy independently of release maturity."""

from __future__ import annotations

from typing import cast

from scripts.app_build.release_identity import ReleaseIdentity


def qualification_status(bundle: dict[str, object]) -> dict[str, object]:
    """Use shipped identity, never the checkout version or an external UI trial."""
    public, build = bundle.get("public_version"), bundle.get("build_number")
    if not isinstance(public, str) or type(build) is not int:
        raise ValueError("Native qualification requires public version and build")
    identity = ReleaseIdentity(public, build)
    version = bundle.get("release_version")
    if version == f"{identity.version}-dev":
        channel = "development"
    elif version == identity.version:
        identity.require_release()
        channel = "prerelease" if identity.prerelease else "release"
    else:
        raise ValueError("Native qualification release identity mismatch")
    target = bundle.get("target")
    if not isinstance(target, dict):
        raise ValueError("Unsupported native qualification target")
    platform = cast("dict[str, object]", target).get("platform")
    if platform not in {"darwin", "win32"}:
        raise ValueError("Unsupported native qualification target")
    macos = platform == "darwin"
    return {
        "channel": channel,
        "signing": {
            "application": "ad-hoc" if macos else "unsigned",
            "installer": "unsigned",
        },
        "notarization": "not-notarized" if macos else "not-applicable",
        "native_interaction": "not-evaluated",
    }


def validate_qualification(
    document: dict[str, object], bundle: dict[str, object]
) -> None:
    """Accept immutable legacy evidence without interpreting its mixed prose."""
    version = document.get("format")
    if type(version) is not int or version not in {1, 2}:
        raise ValueError("Unsupported native qualification format")
    if version == 1:
        if {"channel", "signing", "notarization"} & document.keys():
            raise ValueError("Legacy native qualification has format-2 fields")
        return
    expected = qualification_status(bundle)
    if "distribution" in document or any(
        document.get(key) != value for key, value in expected.items()
    ):
        raise ValueError("Native qualification status mismatch")

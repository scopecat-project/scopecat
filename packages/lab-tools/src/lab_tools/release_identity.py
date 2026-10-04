"""Public release identity, independent of internal package base versions."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

VERSION = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-(alpha|beta|rc)\.([1-9][0-9]*))?"
)


@dataclass(frozen=True, slots=True)
class ReleaseIdentity:
    version: str
    build_number: int

    def __post_init__(self) -> None:
        match = VERSION.fullmatch(self.version)
        if match is None or any(int(match[index]) > 65535 for index in (1, 2, 3)):
            raise ValueError(
                "Public version requires X.Y.Z or X.Y.Z-alpha.N/beta.N/rc.N"
            )
        if type(self.build_number) is not int or not 0 <= self.build_number <= 9999:
            raise ValueError("Native build_number must be an integer in 0..9999")

    def require_release(self) -> None:
        if self.short_version == "0.0.0" or self.build_number == 0:
            raise ValueError(
                "Release requires an explicit version and positive build_number"
            )

    @property
    def short_version(self) -> str:
        return self.version.split("-", 1)[0]

    @property
    def prerelease(self) -> bool:
        return "-" in self.version

    @property
    def pep440(self) -> str:
        return (
            self.version.replace("-alpha.", "a")
            .replace("-beta.", "b")
            .replace("-rc.", "rc")
        )

    @property
    def windows_version(self) -> str:
        return f"{self.short_version}.{self.build_number}"

    def python_package_version(self, base: str, commit: str, timestamp: str) -> str:
        return f"{base}.dev{timestamp}+scopecat.{self.pep440}.g{commit[:12]}"

    def javascript_package_version(self, base: str, commit: str) -> str:
        return f"{base}-scopecat.{self.version.replace('-', '.')}.g{commit[:12]}"


def read_release(path: Path, *, require_release: bool = False) -> ReleaseIdentity:
    document = cast(
        "dict[str, object]", tomllib.loads(path.read_text(encoding="utf-8"))
    )
    version, build_number = document.get("version"), document.get("build_number")
    if not isinstance(version, str) or type(build_number) is not int:
        raise ValueError("release.toml requires version and integer build_number")
    identity = ReleaseIdentity(version, build_number)
    if require_release:
        identity.require_release()
    return identity


def read_source_identity(path: Path, *, release: bool = False) -> ReleaseIdentity:
    """Historical sources may build previews; publication always needs identity."""
    if not release and not path.exists():
        return ReleaseIdentity("0.0.0", 0)
    return read_release(path, require_release=release)

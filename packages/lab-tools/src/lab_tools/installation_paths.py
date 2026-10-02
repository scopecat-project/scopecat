"""Platform installation locations, separate from foreground source development."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

from platformdirs import user_cache_path, user_data_path


@dataclass(frozen=True)
class InstallationPaths:
    state: Path
    cache: Path
    workspace: Path
    entry: Path | None

    @classmethod
    def isolated(cls, home: Path) -> InstallationPaths:
        """Keep explicit test installations entirely inside their requested root."""
        home = home.resolve()
        entry = (
            home / "Scopecat.app"
            if sys.platform == "darwin"
            else home / "Scopecat.lnk"
            if sys.platform == "win32"
            else None
        )
        return cls(
            home / "data",
            home / "cache",
            home / "experiments",
            entry,
        )

    @classmethod
    def current_user(cls) -> InstallationPaths:
        state = user_data_path("Scopecat", appauthor=False, roaming=False)
        cache = user_cache_path("Scopecat", appauthor=False)
        workspace = Path.home() / "Scopecat" / "experiments"
        if sys.platform == "darwin":
            entry = Path.home() / "Applications" / "Scopecat.app"
        elif sys.platform == "win32":
            entry = (
                _windows_folder("A77F5D77-2E2B-44C3-A6A2-ABA601054A51") / "Scopecat.lnk"
            )
        else:
            entry = None
        return cls(state, cache, workspace, entry)


def _windows_folder(identity: str) -> Path:
    """Resolve redirected per-user program/Start Menu locations through Shell32."""
    import ctypes

    identifier = ctypes.create_string_buffer(UUID(identity).bytes_le)
    path = ctypes.c_wchar_p()
    resolve = ctypes.windll.shell32.SHGetKnownFolderPath
    resolve.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    resolve.restype = ctypes.c_long
    free = ctypes.windll.ole32.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = None
    try:
        # KF_FLAG_DONT_VERIFY: return the configured location before first install.
        result = cast(
            "int", resolve(ctypes.byref(identifier), 0x4000, None, ctypes.byref(path))
        )
        if result != 0:
            raise OSError(f"无法定位 Windows 应用目录：HRESULT {result:#x}")
        return Path(cast("str", path.value))
    finally:
        free(path)


class Arguments(Protocol):
    home: Path | None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, help="Isolated installation root")
    args = cast("Arguments", cast("object", parser.parse_args()))
    paths = (
        InstallationPaths.isolated(args.home)
        if args.home
        else InstallationPaths.current_user()
    )
    print(
        json.dumps(
            {
                name: str(value) if value is not None else None
                for name, value in (
                    ("state", paths.state),
                    ("cache", paths.cache),
                    ("workspace", paths.workspace),
                    ("entry", paths.entry),
                )
            }
        )
    )


if __name__ == "__main__":
    main()

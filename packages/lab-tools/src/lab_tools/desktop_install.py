"""Install native, console-free entry points without launching the application."""

from __future__ import annotations

import json
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def install_entry(home: Path, python: Path) -> Path | None:
    if sys.platform == "darwin":
        app = home / "Scopecat.app"
        contents = app / "Contents"
        executable = contents / "MacOS" / "Scopecat"
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text(
            "#!/bin/sh\nexec "
            + shlex.quote(str(python))
            + " "
            + shlex.quote(str(home / "lab.py"))
            + " --action desktop\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        with (contents / "Info.plist").open("wb") as stream:
            plistlib.dump(
                {
                    "CFBundleIdentifier": "org.scopecat.desktop",
                    "CFBundleName": "Scopecat",
                    "CFBundleExecutable": "Scopecat",
                    "CFBundlePackageType": "APPL",
                    "NSHighResolutionCapable": True,
                },
                stream,
            )
        return app
    if sys.platform == "win32":
        powershell = shutil.which("powershell.exe")
        if powershell is None:
            raise ValueError("创建桌面入口需要 Windows PowerShell")
        link = home / "Scopecat.lnk"
        # JSON is a data file, never interpolated into PowerShell program text.
        config = home / ".desktop-entry.json"
        config.write_text(
            json.dumps(
                {
                    "link": str(link),
                    "python": str(python.with_name("pythonw.exe")),
                    "arguments": subprocess.list2cmdline(
                        [str(home / "lab.py"), "--action", "desktop"]
                    ),
                    "home": str(home),
                }
            ),
            encoding="utf-8",
        )
        try:
            subprocess.run(  # noqa: S603 - fixed PowerShell program, paths passed as data
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    (
                        "$c = Get-Content -LiteralPath .desktop-entry.json "
                        "-Raw -Encoding UTF8 | ConvertFrom-Json; "
                        "$s = (New-Object -ComObject WScript.Shell)"
                        ".CreateShortcut($c.link); "
                        "$s.TargetPath = $c.python; $s.Arguments = $c.arguments; "
                        "$s.WorkingDirectory = $c.home; $s.Save()"
                    ),
                ],
                cwd=home,
                check=True,
            )
        finally:
            config.unlink(missing_ok=True)
        return link
    return None

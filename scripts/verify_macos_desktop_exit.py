"""Exercise the packaged Mac host through Cocoa teardown and process exit."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROBE = """
import webview

window = webview.create_window(
    "Scopecat exit qualification", html="<p>Exit</p>", hidden=True
)

def close():
    if not window.events.loaded.wait(15):
        raise RuntimeError("Native window did not load")
    window.destroy()

webview.start(close)
"""


def verify(app: Path) -> None:
    app = app.resolve()
    with tempfile.TemporaryDirectory(prefix="scopecat-native-exit-") as temporary:
        probe = Path(temporary) / "Scopecat.app/Contents"
        resources = probe / "Resources"
        resources.mkdir(parents=True)
        executable = probe / "MacOS/Scopecat"
        executable.parent.mkdir()
        shutil.copy2(app / "Contents/MacOS/Scopecat", executable)
        shutil.copy2(app / "Contents/Info.plist", probe / "Info.plist")
        (resources / "python").symlink_to(
            app / "Contents/Resources/python", target_is_directory=True
        )
        (resources / "bootstrap.py").write_text(PROBE, encoding="utf-8")
        # Running the packaged native executable catches pools drained after
        # Python finalization; invoking the bundled python misses this crash.
        # --check-result suppresses the launcher's modal failure alert.
        for _ in range(2):
            _ = subprocess.run(  # noqa: S603 - isolated packaged native host
                [str(executable), "--check-result"], check=True, timeout=45
            )
    print("PASS: packaged Cocoa window teardown and process exit (twice)")


if __name__ == "__main__":
    verify(Path(sys.argv[1]))

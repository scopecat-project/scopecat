"""Regenerate checked-in native icons from assets/branding/app-icon.svg.

Requires ImageMagick on the maintainer's machine and Pillow from the delivery
group. Packaging and application startup use the generated files directly.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "assets/branding/app-icon.svg"
    output = root / "packages/lab-tools/src/lab_tools/icons"
    output.mkdir(exist_ok=True)
    renderer = shutil.which("magick")
    if renderer is None:
        raise RuntimeError("Install ImageMagick to regenerate the application icons")
    with tempfile.TemporaryDirectory(prefix="scopecat-icons-") as directory:
        rendered = Path(directory) / "icon.png"
        _ = subprocess.run(  # noqa: S603 - explicit maintainer asset-generation tool
            [
                renderer,
                "-background",
                "none",
                f"MSVG:{source}",
                "-resize",
                "1024x1024",
                str(rendered),
            ],
            check=True,
        )
        with Image.open(rendered) as image:
            icon = image.convert("RGBA")
        icon.save(output / "Scopecat.icns", format="ICNS")
        icon.save(
            output / "Scopecat.ico",
            format="ICO",
            sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)],
        )
        # Remove the large application-icon margins for the small status item.
        tray = icon.crop(icon.getbbox())
        canvas = Image.new("RGBA", (64, 64))
        tray.thumbnail((60, 60), Image.Resampling.LANCZOS)
        canvas.alpha_composite(tray, ((64 - tray.width) // 2, (64 - tray.height) // 2))
        canvas.save(output / "tray.png")
        # Render above the target resolution so the template keeps smooth edges.
        _ = subprocess.run(  # noqa: S603 - fixed maintainer asset paths
            [
                renderer,
                "-background",
                "none",
                f"MSVG:{root / 'assets/branding/menu-bar-icon.svg'}",
                "-resize",
                "64x64",
                str(output / "tray-template.png"),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()

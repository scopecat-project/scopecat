# Application icon

`app-icon.svg` is the source for the cat-shaped oscilloscope application icon.
The native application and Windows tray use generated resources from this drawing.
`menu-bar-icon.svg` is the simplified, symmetric single-peak variant for macOS.

After editing the SVG, run `uv run --group delivery python scripts/generate_app_icons.py`
with ImageMagick available. Commit the generated files under
`packages/lab-tools/src/lab_tools/icons/` together with the source change.
Ordinary builds and installed applications do not need an SVG renderer.

The macOS bundle uses `Scopecat.icns`; the Windows executable embeds
`Scopecat.ico`. The tray uses `tray.png` with reduced outer margins for small sizes.
The Mac menu bar uses `tray-template.png` as a Cocoa template image, so the system
supplies its light, dark and selected appearance from the image's alpha channel.
Application icons are copied before signing the package.

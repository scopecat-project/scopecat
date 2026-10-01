# Application icon

`app-icon.svg` is the source for the cat-shaped oscilloscope application icon.
The native application and tray use generated resources from this same drawing.

After editing the SVG, run `uv run --group delivery python scripts/generate_app_icons.py`
with ImageMagick available. Commit the generated files under
`packages/lab-tools/src/lab_tools/icons/` together with the source change.
Ordinary builds and installed applications do not need an SVG renderer.

The macOS bundle uses `Scopecat.icns`; the Windows executable embeds
`Scopecat.ico`. The tray uses `tray.png` with reduced outer margins for small sizes.
Application icons are copied before signing the package.

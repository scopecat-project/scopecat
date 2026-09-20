"""Locate installed workbench assets without importing CLI entry points."""

from pathlib import Path

_DEFAULT_STATIC_DIR = Path(__file__).with_name("static")


def select_static_dir(
    *,
    static_dir: Path | None,
    api_only: bool,
) -> Path | None:
    if api_only:
        if static_dir is not None:
            raise ValueError("--api-only and --static-dir cannot be used together")
        return None
    selected = _DEFAULT_STATIC_DIR if static_dir is None else static_dir.resolve()
    if not (selected / "index.html").is_file():
        raise ValueError(
            "GUI bundle is not installed; pass its directory with --static-dir "
            "or use --api-only"
        )
    return selected

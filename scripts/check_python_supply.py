"""Check the real pinned Python supply on each CI target without downloading it."""

import json
from pathlib import Path

from scripts.app_build.toolchain import select_runtime


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    selected = select_runtime(repository)
    if selected["variant"] != "default":
        raise ValueError("Supported application targets require ordinary CPython")
    print(json.dumps(selected, indent=2))


if __name__ == "__main__":
    main()

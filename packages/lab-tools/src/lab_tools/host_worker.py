"""A reconnectable operation survives its management HTTP process restarting."""

import argparse
from pathlib import Path
from typing import Protocol, cast

from .bundle import configure_console
from .host_operations import Operations, execute


class Arguments(Protocol):
    home: Path
    operation: str
    source: Path | None


def main() -> None:
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("home", type=Path)
    parser.add_argument("operation")
    parser.add_argument("--source", type=Path)
    args = cast("Arguments", cast("object", parser.parse_args()))
    store = Operations(args.home)
    operation = store.claim(args.operation)
    try:
        operation.workspace = execute(args.home, args.source, operation.command)
        operation.status = "succeeded"
        operation.detail = "已完成"
    except Exception as error:
        import traceback

        traceback.print_exc()
        operation.status = "failed"
        operation.detail = str(error)
    finally:
        store.save(operation)


if __name__ == "__main__":
    main()

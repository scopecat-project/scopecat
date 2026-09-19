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
        operation.detail = {
            "service_stop": (
                "实验服务已停止。项目和科学记录保留；重新测量前请打开工作台。"
            ),
            "service_recheck": (
                "环境复检成功，登记身份已更新。实验服务保持停止，可显式启动。"
            ),
            "service_remove": (
                "已移除服务登记。项目和科学数据没有删除，可从本机 CLI 重新登记。"
            ),
        }.get(operation.command.action, "已完成")
    except Exception as error:
        import traceback

        traceback.print_exc()
        operation.status = "failed"
        operation.detail = str(error)
    finally:
        store.save(operation)


if __name__ == "__main__":
    main()

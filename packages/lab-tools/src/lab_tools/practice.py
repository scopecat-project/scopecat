"""在已有应用中开始或清理练习；不创建教程服务或 Python 环境。"""

import argparse
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from lab_tools.application_runtime import ApplicationRuntime
from scopecat.daemon.client import DaemonClient
from scopecat.records.practice import PracticeClearCommand, PracticeCreateCommand


class Arguments(Protocol):
    home: Path
    list: bool
    clear: str | None
    files: str
    request_key: str


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True, help="已安装应用的目录")
    parser.add_argument("--list", action="store_true", help="列出已有练习")
    parser.add_argument("--clear", help="清理指定练习的 ID")
    parser.add_argument("--files", choices=("preserve", "discard"), default="preserve")
    parser.add_argument("--request-key", default=uuid4().hex)
    args = cast("Arguments", cast("object", parser.parse_args(argv)))
    try:
        endpoint = ApplicationRuntime(args.home).start()
        with DaemonClient(endpoint.base_url) as client:
            if args.list:
                for scope in client.practices().items:
                    print(
                        f"{scope.id} · {scope.title} · {scope.state}\n{scope.directory}"
                    )
            elif args.clear is not None:
                scope = client.clear_practice(
                    args.clear,
                    PracticeClearCommand.model_validate({"files": args.files}),
                )
                print(
                    f"练习已清理；文件处理：{scope.file_disposition}\n{scope.directory}"
                )
            else:
                scope = client.create_practice(
                    PracticeCreateCommand(request_key=args.request_key)
                )
                print(
                    f"练习：{scope.id}\n{endpoint.base_url}/?procedure={scope.procedure_id}#launch"
                )
                print(
                    f"可选笔记目录：{scope.directory}\n"
                    "在应用 Help 中继续或清理；不会打开浏览器。"
                )
    except (ValueError, OSError) as error:
        parser.exit(2, f"{error}\n应用和已有数据保留，请在应用中检查后重试。\n")


if __name__ == "__main__":
    main()

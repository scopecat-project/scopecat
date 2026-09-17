"""Check relative links in repository-authored Markdown documents."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "node_modules",
        "site",
        "results",
        "dist",
    }
)
_FENCE = re.compile(r"^\s*(```|~~~)")
_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\((?P<target><[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)")


def _markdown_files(root: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(root.rglob("*.md"))
        if not _IGNORED_DIRECTORIES.intersection(path.relative_to(root).parts)
    )


def _links_outside_fences(text: str) -> tuple[tuple[int, str], ...]:
    links: list[tuple[int, str]] = []
    fence: str | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        marker = _FENCE.match(line)
        if marker is not None:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token == fence:
                fence = None
            continue
        if fence is not None:
            continue
        links.extend(
            (line_number, match.group("target"))
            for match in _MARKDOWN_LINK.finditer(line)
        )
    return tuple(links)


def _relative_target(raw_target: str) -> str | None:
    target = raw_target.removeprefix("<").removesuffix(">")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    return unquote(parsed.path)


def broken_document_links(root: Path) -> tuple[str, ...]:
    """Return diagnostics for relative Markdown links whose target is missing."""

    problems: list[str] = []
    for source in _markdown_files(root):
        text = source.read_text(encoding="utf-8")
        for line_number, raw_target in _links_outside_fences(text):
            relative_target = _relative_target(raw_target)
            if relative_target is None:
                continue
            destination = (source.parent / relative_target).resolve()
            if not destination.is_relative_to(root) or not destination.exists():
                source_name = source.relative_to(root)
                problems.append(
                    f"{source_name}:{line_number}: missing link target {raw_target!r}"
                )
    return tuple(problems)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    problems = broken_document_links(root)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("All relative Markdown link targets exist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

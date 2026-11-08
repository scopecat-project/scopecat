"""Require Renovate-readable version comments on SHA-pinned workflow actions.

This is an offline annotation check, not verification that an upstream tag still
resolves to the pinned commit. Verify that correspondence when updating a pin.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-fA-F]{40}")
# Renovate accepts major/minor comments too; retain existing # v5 / # v6 pins.
VERSION_COMMENT = re.compile(
    r"\s+#\s+v?\d+(?:\.\d+){0,2}(?:-[\w.-]+)?(?:\+[\w.-]+)?(?:\s|$)"
)


def field(node: Node | None, name: str) -> Node | None:
    if isinstance(node, MappingNode):
        for key, value in mapping_items(node):
            if isinstance(key, ScalarNode) and scalar_value(key) == name:
                return value
    return None


def mapping_items(node: MappingNode) -> list[tuple[Node, Node]]:
    # PyYAML's node stubs erase the composer's known mapping/sequence/scalar types.
    return node.value  # pyright: ignore[reportAny] -- composed mappings contain node pairs.


def sequence_items(node: SequenceNode) -> list[Node]:
    return node.value  # pyright: ignore[reportAny] -- composed sequences contain nodes.


def scalar_value(node: ScalarNode) -> str:
    return node.value  # pyright: ignore[reportAny] -- composed scalars are source strings.


def action_uses(document: Node | None) -> Iterator[ScalarNode]:
    """Read actual action/reusable-workflow uses, not shell text or input values."""
    jobs = field(document, "jobs")
    if not isinstance(jobs, MappingNode):
        return
    for _, job in mapping_items(jobs):
        uses = field(job, "uses")
        if isinstance(uses, ScalarNode):
            yield uses
        steps = field(job, "steps")
        if isinstance(steps, SequenceNode):
            for step in sequence_items(steps):
                uses = field(step, "uses")
                if isinstance(uses, ScalarNode):
                    yield uses


def check_workflow(source: str, path: str) -> list[str]:
    """Return file/line diagnostics for pins without an inline version comment."""
    errors: list[str] = []
    loader = yaml.SafeLoader(source)
    try:
        document = loader.get_single_node()
    finally:
        loader.dispose()
    lines = source.splitlines()
    for uses in action_uses(document):
        action = scalar_value(uses)
        if not PIN.fullmatch(action):
            continue
        start, end = uses.start_mark, uses.end_mark
        assert start is not None and end is not None
        if start.line == end.line and VERSION_COMMENT.match(
            lines[end.line][end.column :]
        ):
            continue
        errors.append(
            f"{path}:{start.line + 1}: {action} needs an inline version comment "
            "(for example # v1.2.3) so Renovate can track this SHA pin"
        )
    return errors


def main(root: Path = ROOT) -> int:
    directory = root / ".github/workflows"
    paths = sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")])
    if not paths:
        print(f"No workflow files found in {directory}")
        return 1
    errors: list[str] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        try:
            errors.extend(check_workflow(path.read_text(encoding="utf-8"), relative))
        except yaml.YAMLError as error:
            errors.append(f"{relative}: invalid YAML: {error}")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Action pin version comments checked in {len(paths)} workflows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

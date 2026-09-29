"""Build one editable capability into a qualified immutable development snapshot."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

from packaging.utils import canonicalize_name
from uv import find_uv_bin

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.project import open_project

from .bundle import MANIFEST, RECEIPT, check_receipt, file_hash, verify_bundle
from .delivery import MODULES, wheel_metadata

if TYPE_CHECKING:
    from .application_runtime import ApplicationRuntime, Installation


def prepare_capability(runtime: ApplicationRuntime, source: Path) -> Installation:
    """Reuse the selected delivery's dependencies and GUI; never edit its venv."""
    source = source.resolve()
    if not (source / "pyproject.toml").is_file():
        raise ValueError("请选择含 pyproject.toml 的能力包源码目录")
    selected = runtime.installation()
    reference = open_project(runtime.root, resolve_adapter=False).lab_adapter
    if reference is None:
        raise ValueError("请先安装能力包交付，再选择它的开发源码")
    environment = selected.python.parent.parent
    receipt = cast("dict[str, str]", json.loads((environment / RECEIPT).read_text()))
    baseline = Path(receipt["bundle"])
    check_receipt(environment, baseline)
    manifest = verify_bundle(baseline)
    uv = find_uv_bin()
    with tempfile.TemporaryDirectory(prefix="scopecat-capability-") as directory:
        staging = Path(directory)
        wheels = staging / "built"
        subprocess.run(  # noqa: S603 - explicit local build, no shell or live edits
            [
                uv,
                "build",
                "--wheel",
                "--no-sources",
                "--offline",
                "--no-index",
                "--find-links",
                (baseline / "wheels").as_uri(),
                "--build-constraints",
                (baseline / "requirements.lock").as_uri(),
                "--out-dir",
                str(wheels),
                str(source),
            ],
            check=True,
            env=dict(os.environ, SOURCE_DATE_EPOCH="315532800"),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        built = list(wheels.glob("*.whl"))
        if len(built) != 1:
            raise ValueError("能力源码必须构建恰好一个 wheel")
        wheel = built[0]
        name, _ = wheel_metadata(wheel)
        if canonicalize_name(name) != canonicalize_name(reference.distribution):
            raise ValueError("开发源码不属于当前选定的能力包；没有切换应用")
        if canonicalize_name(name) in MODULES.values():
            raise ValueError("科学框架包需要完整交付构建，不能作为可选能力替换")
        candidate = staging / "delivery"
        shutil.copytree(baseline, candidate)
        inventory = dict(manifest["files"])
        for path in (candidate / "wheels").glob("*.whl"):
            if canonicalize_name(wheel_metadata(path)[0]) == canonicalize_name(name):
                del inventory[path.relative_to(candidate).as_posix()]
                path.unlink()  # Only the temporary candidate copy, never a release.
        shutil.copyfile(wheel, candidate / "wheels" / wheel.name)
        inventory[f"wheels/{wheel.name}"] = file_hash(wheel)
        requirements: list[str] = []
        for path in (candidate / "wheels").glob("*.whl"):
            package, version = wheel_metadata(path)
            requirements.append(f"{package}=={version} --hash=sha256:{file_hash(path)}")
        (candidate / "requirements.lock").write_text(
            "\n".join(sorted(requirements)) + "\n", encoding="utf-8"
        )
        inventory["requirements.lock"] = file_hash(candidate / "requirements.lock")
        document = cast(
            "dict[str, object]", json.loads((baseline / MANIFEST).read_text())
        )
        document.update(
            files=inventory,
            release_kind="development",
            sources={**manifest["sources"], "capability": str(source)},
            build_id=sha256_json_hash(
                {"files": inventory, "target": manifest["target"]}
            ),
        )
        (candidate / MANIFEST).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return runtime.prepare_update(candidate)

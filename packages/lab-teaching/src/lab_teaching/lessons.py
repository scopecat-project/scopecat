"""Fresh editable material shared by default and topic teaching entries."""

import json
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

TOPICS = {
    "parameters": "参数与扫描",
    "compute": "平均 IQ 与类型化读取",
    "refresh": "修改、刷新和新增实验",
    "groups": "分组分析与历史",
    "calibration": "参数校准与恢复",
    "joint-calibration": "联合校准与耦合验证",
    "task-calibration": "后台标定与最终发布",
}


def install_lesson(root: Path, topic: str | None = None) -> Path:
    if topic is not None and topic not in TOPICS:
        raise ValueError(f"未知专题: {topic}; 可选 {', '.join(TOPICS)}")
    # Only fresh scaffolds are admitted. Never replace an author's edits or notes.
    source = root / "src/my_experiment"
    if (
        any(path.name != "__init__.py" for path in source.iterdir())
        or any((root / "notebooks").iterdir())
        or (root / "src/workspace_app.py").exists()
        or (root / "README.md").exists()
        or (topic == "refresh" and (root / "examples").exists())
    ):
        raise FileExistsError(f"教学目录已有内容，未覆盖：{root}")
    material = files("lab_teaching.course_material")
    lesson = material.joinpath("lessons")
    for name in ("parameters", "setup", "response"):
        _ = (root / f"src/my_experiment/{name}.py").write_bytes(
            lesson.joinpath(f"{name}.py.txt").read_bytes()
        )
    _ = (root / "src/workspace_app.py").write_bytes(
        lesson.joinpath("workspace_app.py.txt").read_bytes()
    )
    _ = (root / "src/my_experiment/teaching.py").write_bytes(
        lesson.joinpath("experiment.py.txt").read_bytes()
    )
    for name in ("group_analysis", "result_types"):
        _ = (source / f"{name}.py").write_bytes(
            material.joinpath(f"{name}.py").read_bytes()
        )
    if topic is None:
        for name in ("analysis", "session"):
            _ = (source / f"{name}.py").write_bytes(
                lesson.joinpath(f"{name}.py.txt").read_bytes()
            )
        for name in (
            "start.ipynb",
            "reopen.ipynb",
            "HINTS.md",
            "REFERENCE.md",
            "OBSERVATION.md",
            "EDITING.md",
            "GROUPS.md",
            "MAINTENANCE.md",
        ):
            _ = (root / "notebooks" / name).write_bytes(
                material.joinpath(name).read_bytes()
            )
        readme = material.joinpath("README.md").read_text(encoding="utf-8")
        for name in ("EDITING.md", "GROUPS.md", "MAINTENANCE.md"):
            readme = readme.replace(f"]({name})", f"](notebooks/{name})")
        _ = (root / "README.md").write_text(readme, encoding="utf-8")
        return root / "notebooks/start.ipynb"
    notebook = root / "notebooks" / f"{topic}.ipynb"
    _ = notebook.write_bytes(lesson.joinpath(f"{topic}.ipynb").read_bytes())
    if topic == "parameters":
        _ = (root / "src/my_experiment/setup.py").write_bytes(
            lesson.joinpath("parameters_setup.py.txt").read_bytes()
        )
        _ = (root / "src/my_experiment/lesson_identity.py").write_text(
            f'IDENTITY = "parameters-{uuid4().hex}"\n', encoding="utf-8"
        )
    if topic == "compute":
        _ = (root / "src/my_experiment/teaching.py").write_bytes(
            lesson.joinpath("compute_experiment.py.txt").read_bytes()
        )
    if topic in ("calibration", "joint-calibration", "task-calibration"):
        _ = (root / "src/my_experiment/calibration.py").write_bytes(
            lesson.joinpath("calibration.py.txt").read_bytes()
        )
        (root / "src/my_experiment/teaching.py").unlink()
        procedure = "my_experiment.calibration:calibrate"
        if topic in ("joint-calibration", "task-calibration"):
            _ = (root / "src/my_experiment/joint_calibration.py").write_bytes(
                lesson.joinpath("joint_calibration.py.txt").read_bytes()
            )
            procedure = "my_experiment.joint_calibration:calibrate_joint"
        procedures = [procedure, "my_experiment.calibration:check_zero"]
        if topic == "task-calibration":
            _ = (root / "src/my_experiment/task_calibration.py").write_bytes(
                lesson.joinpath("task_calibration.py.txt").read_bytes()
            )
            procedures = [
                "my_experiment.task_calibration:fit_stage",
                "my_experiment.task_calibration:finalize",
            ]
        manifest = root / "scopecat.toml"
        _ = manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                "[lab.capabilities]",
                f"[lab.capabilities]\nprocedures = {json.dumps(procedures)}",
            ),
            encoding="utf-8",
        )
    if topic == "refresh":
        (root / "examples").mkdir()
        extra = lesson.joinpath("experiment.py.txt").read_text(encoding="utf-8")
        _ = (root / "examples/extra.py").write_text(
            extra.replace('id="teaching.rabi"', 'id="teaching.extra"').replace(
                "def teaching_rabi(", "def extra_rabi("
            ),
            encoding="utf-8",
        )
    _ = (root / "README.md").write_text(
        f"# {TOPICS[topic]}\n\n打开 [专题 Notebook](notebooks/{topic}.ipynb), "
        "选择本项目 .venv 内核, 从头运行即可。无需其他课程的结果。\n\n"
        "这是可重建的合成沙盒, 不连接设备。重复打开保留当前练习; "
        "重置建立新的副本, 不必合并旧练习。需要留存时手动复制源码和 Notebook。\n",
        encoding="utf-8",
    )
    if topic == "parameters":
        _ = (root / "README.md").write_text(
            "# 参数与扫描\n\n从 Scopecat Help 开始或继续。"
            "打开 notebooks/parameters.ipynb，"
            "在外部编辑器选择此目录 .venv 内核。源码在 src/my_experiment。\n"
            "运行与历史保存在同一应用中；继续不会自动采集，也不会覆盖文件或保存的参数。\n"
            "这是普通、可编辑的作者目录。应用 Data 管理运行记录；"
            "删除代码前自行保留修改。\n",
            encoding="utf-8",
        )
    return notebook

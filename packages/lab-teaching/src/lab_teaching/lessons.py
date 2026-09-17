"""独立专题的官方 Notebook 与源码起点。"""

from importlib.resources import files
from pathlib import Path

TOPICS = {
    "parameters": "参数与扫描",
    "compute": "平均 IQ 与类型化读取",
    "refresh": "修改、刷新和新增实验",
    "groups": "分组分析与历史",
}


def install_lesson(root: Path, topic: str) -> Path:
    if topic not in TOPICS:
        raise ValueError(f"未知专题: {topic}; 可选 {', '.join(TOPICS)}")
    material = files("lab_teaching.course_material")
    lesson = material.joinpath("lessons")
    # create_project has just generated this directory; no user files exist yet.
    for previous in (root / "notebooks").iterdir():
        previous.unlink()
    notebook = root / "notebooks" / f"{topic}.ipynb"
    _ = notebook.write_bytes(lesson.joinpath(f"{topic}.ipynb").read_bytes())
    if topic == "compute":
        _ = (root / "src/my_experiment/teaching.py").write_bytes(
            lesson.joinpath("compute_experiment.py.txt").read_bytes()
        )
    if topic == "refresh":
        (root / "examples").mkdir()
        extra = material.joinpath("experiment.py").read_text(encoding="utf-8")
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
    return notebook

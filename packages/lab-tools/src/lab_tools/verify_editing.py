"""Installed editing checks; each entry becomes a retained notebook cell."""

CONNECTION = """\
import json
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
import scopecat as sc
from lab_teaching.session import open_parameters

project = sc.open_project()
session = project.authoring()
"""

READ_TYPES = """\
from my_experiment.result_types import IQ

@dataclass(frozen=True)
class MeanRow:
    amplitude: sc.Quantity
    iq: IQ

@dataclass(frozen=True)
class ShotRow:
    amplitude: sc.Quantity
    iq: NDArray[np.complex128]
"""

EDIT_CELLS = (
    CONNECTION
    + """\
session.refresh()
from my_experiment.teaching import teaching_rabi
params = open_parameters(session)
request_a = teaching_rabi()
request_a.values["amplitude"] = sc.Scan(np.linspace(0, 0.8, 7))
assert request_a.values["seed"] == 200
prepared_a = session.prepare(request_a, parameters=params)
assert prepared_a.preview.point_count == 7
run_a = prepared_a.run().wait(timeout=120).result()
print("修改前 run:", run_a.id)
""",
    """\
# 自动执行指南 A 中由人类编辑器完成的修改。
source = project.root / "src/my_experiment/teaching.py"
original = source.read_text(encoding="utf-8")
assert "seed: int = 200" in original
source.write_text(original.replace("seed: int = 200", "seed: int = 400"),
                  encoding="utf-8")
session.refresh()
from my_experiment.teaching import teaching_rabi
request_b = teaching_rabi()
request_b.values["amplitude"] = sc.Scan(np.linspace(0, 0.8, 7))
assert request_a.values["seed"] == 200
assert request_b.values["seed"] == 400
assert request_a.declaration.code_revision != request_b.declaration.code_revision
assert session.prepare(request_a, parameters=params).preview.code_revision == (
    prepared_a.preview.code_revision
)
run_b = session.prepare(request_b, parameters=params).run().wait(timeout=120).result()
assert not np.array_equal(run_a.measurements()["iq"].require_values(),
                          run_b.measurements()["iq"].require_values())
print("修改后 run:", run_b.id)
""",
    '''\
# 自动执行指南 B 的三个源码编辑;计算函数与指南保持一致。
text = source.read_text(encoding="utf-8")
helper = """
import numpy as np
from numpy.typing import NDArray
from my_experiment.result_types import IQ

@sc.compute
def mean_iq(iq: NDArray[np.complex128]) -> IQ:
    return complex(iq.mean())
"""
assert "iq: sc.ProductRef" in text
assert 'cast("sc.ProductRef", iq))' in text
text = text.replace("from lab_teaching.synthetic import response",
                    "from lab_teaching.synthetic import response\\n" + helper)
text = text.replace("iq: sc.ProductRef", "iq: sc.DataRef[complex]")
text = text.replace('cast("sc.ProductRef", iq))', 'mean_iq(cast("sc.ProductRef", iq)))')
# 保留一次真实的作者计算失败,修复后继续使用同一会话。
from scopecat.application.author_project import AuthorJobFailed
broken = text.replace("return complex(iq.mean())",
                      'raise ValueError("teaching mean deliberate failure")')
source.write_text(broken, encoding="utf-8")
session.refresh()
from my_experiment.teaching import teaching_rabi
failed_job = session.prepare(teaching_rabi(), parameters=params).run()
try:
    failed_job.wait(timeout=120)
except AuthorJobFailed as error:
    assert "teaching mean deliberate failure" in str(error), str(error)
    print("预期的计算错误:", error)
else:
    raise AssertionError("错误计算应失败并向 Notebook 显示原始原因")
source.write_text(text, encoding="utf-8")
session.refresh()
from my_experiment.teaching import teaching_rabi
request_mean = teaching_rabi()
request_mean.values["amplitude"] = sc.Scan(np.linspace(0, 0.8, 7))
assert request_mean.values["seed"] == 400
prepared_mean = session.prepare(request_mean, parameters=params)
run_mean = prepared_mean.run().wait(timeout=120).result()
print("平均 IQ run:", run_mean.id)
''',
    READ_TYPES
    + """\
rows = run_mean.result().rows_as(MeanRow)
shots = np.asarray(run_b.measurements()["iq"].require_values())
assert shots.shape == (7, 64)
assert len(rows) == 7
assert all(row.amplitude.unit == "arb" for row in rows)
np.testing.assert_allclose([row.iq for row in rows], shots.mean(axis=1))
# 新增实验文件也使用相同刷新/导入入口。
extra = source.with_name("extra.py")
extra.write_text(source.read_text(encoding="utf-8").replace(
    'id="teaching.rabi"', 'id="teaching.extra"').replace(
    "def teaching_rabi(", "def extra_rabi("), encoding="utf-8")
session.refresh()
from my_experiment.extra import extra_rabi
extra_request = extra_rabi().sweep(amplitude=np.linspace(0, 0.8, 7))
extra_job = session.prepare(extra_request, parameters=params).run()
extra_run = extra_job.wait(timeout=120).result()
assert len(extra_run.result().rows_as(MeanRow)) == 7
(project.root / "editing-runs.json").write_text(
    json.dumps({"before": run_a.id, "shots": run_b.id,
                "mean": run_mean.id, "added": extra_run.id}),
    encoding="utf-8",
)
session.close()
""",
)

REOPEN_CELLS = (
    CONNECTION + READ_TYPES,
    """\
# 服务与 kernel 均已重启;不导入实验声明,也不重新采集。
ids = json.loads((project.root / "editing-runs.json").read_text(encoding="utf-8"))
rows = session.run(ids["mean"]).result().rows_as(MeanRow)
old_rows = session.run(ids["shots"]).result().rows_as(ShotRow)
before = session.run(ids["before"]).result().rows_as(ShotRow)
added = session.run(ids["added"]).result().rows_as(MeanRow)
np.testing.assert_allclose([row.iq for row in added], [row.iq for row in rows])
assert len(rows) == len(old_rows) == len(before) == 7
assert all(row.iq.shape == (64,) for row in old_rows + before)
np.testing.assert_allclose([row.iq for row in rows],
                           [row.iq.mean() for row in old_rows])
try:
    session.run(ids["shots"]).result().rows_as(MeanRow)
except TypeError as error:
    assert "does not match" in str(error), str(error)
    print("预期的不兼容读取:", error)
else:
    raise AssertionError("逐 shot 数组不能按复数标量读取")
# 拒绝错误读取后,原始数据仍可完整读取。
np.testing.assert_array_equal(
    [row.iq for row in session.run(ids["shots"]).result().rows_as(ShotRow)],
    [row.iq for row in old_rows],
)
session.close()
""",
)

"""Actual IPython cells use one workspace while requests retain their source."""

from pathlib import Path

from IPython.core.interactiveshell import InteractiveShell

from lab_teaching.project import create_project
from scopecat.project import open_project
from scopecat_server.lifecycle import start_project, stop_project


def test_notebook_workspace_cells(tmp_path: Path, notebook_imports) -> None:
    import importlib

    notebook_module = importlib.import_module("scopecat.notebook_workspace")
    shell = InteractiveShell()
    notebook_imports.setattr(notebook_module, "_shell", lambda: shell)
    root = tmp_path / "notebook"
    create_project(root)
    project = open_project(root)
    start_project(project, timeout=120)
    shell.user_ns["root"] = root
    other = tmp_path / "other"
    create_project(other)
    shell.user_ns["other"] = other

    def cell(code: str) -> None:
        result = shell.run_cell(code)
        assert result.error_before_exec is None
        assert result.error_in_exec is None

    try:
        cell("""
import scopecat as sc
session = sc.notebook(root)
assert sc.notebook(root) is session
import my_experiment.teaching as experiments
from my_experiment.teaching import teaching_rabi as rabi
from lab_teaching.session import open_parameters
params = open_parameters(session)
collection = session.create_record_collection("Notebook context")
selected = session.use(collection=collection.id, operator="notebook-author")
assert sc.notebook(root).selection == selected
before = rabi()
prepared = session.prepare(before, parameters=params)
generation = session.state().generation
assert experiments.teaching_rabi().values['seed'] == 200
assert session.state().generation == generation
assert 'live' in repr(session)
assert '<pre>' in session._repr_html_()
try:
    sc.notebook(other)
except ValueError as error:
    assert 'Close' in str(error)
else:
    raise AssertionError('workspace switched implicitly')
""")
        source = root / "src/my_experiment/teaching.py"
        original = source.read_text(encoding="utf-8")
        changed = original.replace("seed: int = 200", "seed: int = 401")
        source.write_text(changed, encoding="utf-8")
        cell("""
assert rabi().values['seed'] == 401
assert experiments.teaching_rabi().values['seed'] == 401
assert before.values['seed'] == 200
session.use(operator="after-refresh")
assert session.selection.collection == collection.id
assert prepared.request.actor == "notebook-author"
assert prepared.request.record_collection == collection.id
# Reading an old revision must not redirect subsequent live calls.
pinned = session.load_experiment(
    before.declaration, code_revision=before.declaration.code_revision
)
assert rabi().values['seed'] == 401
assert rabi().declaration.code_revision != before.declaration.code_revision
""")
        # New module import succeeds at the next cell with no refresh/import magic.
        extra = changed.replace('id="teaching.rabi"', 'id="teaching.extra"').replace(
            "def teaching_rabi(", "def extra_rabi("
        )
        (source.parent / "extra.py").write_text(extra, encoding="utf-8")
        cell("""
import my_experiment.extra as extra
assert extra.extra_rabi().declaration.id == 'teaching.extra'
""")
        # A new attribute in an already imported module updates its module alias.
        addition = (
            changed[changed.index("@sc.experiment") :]
            .replace('id="teaching.rabi"', 'id="teaching.another"')
            .replace("def teaching_rabi(", "def new_name(")
        )
        source.write_text(changed + "\n" + addition, encoding="utf-8")
        cell("assert experiments.new_name().values['seed'] == 401")
        cell("assert sc.notebook(root, live=False) is session")
        source.write_text(changed.replace("401", "402"), encoding="utf-8")
        cell("assert rabi().values['seed'] == 401")
        cell(
            "assert sc.notebook(root, live=True) is session\n"
            "assert rabi().values['seed'] == 402"
        )
        source.write_text(changed + "\ninvalid python !\n", encoding="utf-8")
        cell("""
from scopecat.daemon.preparation import AuthorPreparationFailed
try:
    rabi()
except AuthorPreparationFailed:
    pass
else:
    raise AssertionError('invalid saved source silently used old code')
try:
    session.prepare('teaching.rabi', parameters=params)
except AuthorPreparationFailed:
    pass
else:
    raise AssertionError('ID-based preparation silently used old code')
""")
        source.write_text(changed, encoding="utf-8")
        cell("""
assert rabi().values['seed'] == 401
# The old prepared experiment still runs its original source after all edits.
run = prepared.run().wait(timeout=120).result()
assert run.request.operator == "notebook-author"
number = session.run_number(run)
assert session.run(number).id == run.id
run_id = run.id
session.close()
session = sc.notebook(root)
assert session.selection.collection is None
assert session.run(number, collection=collection.id).id == run_id
assert rabi().values['seed'] == 401
assert sc.notebook(root) is session
session.close()
""")
        assert notebook_module._workspace is None
        other_project = open_project(other)
        start_project(other_project, timeout=120)
        try:
            cell("""
session = sc.notebook(other)
try:
    rabi()
except ValueError as error:
    assert 'another workspace' in str(error)
else:
    raise AssertionError('old project alias rebound to a different project')
import my_experiment.teaching as experiments
assert experiments.teaching_rabi().values['seed'] == 200
session.close()
""")
        finally:
            stop_project(other_project)
    finally:
        if notebook_module._workspace is not None:
            notebook_module._workspace.close()
        stop_project(project)

"""Temporary investigation-branch collector for synthetic test daemons."""
import json
import shutil
from pathlib import Path
from scopecat.runtime_binding import load_runtime_binding

for lifetime in Path("test-diagnostics").rglob("daemon-lifetime-*.jsonl"):
    first = json.loads(lifetime.read_text().splitlines()[0])
    root = Path(first["project_root"])
    if not root.exists():
        print(f"{lifetime.name}: test directory already removed")
        continue
    source = load_runtime_binding(root).coordination_root / "daemon.log"
    if source.is_file():
        shutil.copyfile(source, lifetime.with_suffix(".full-stderr.log"))

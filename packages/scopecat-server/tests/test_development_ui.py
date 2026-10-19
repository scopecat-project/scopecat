"""Development handoff uses only a live owner of the exact backend."""

import json
import os
from pathlib import Path
from unittest.mock import Mock

import psutil
import pytest

from scopecat_server import development_ui


def test_live_ui_rejects_stale_and_other_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / ".scopecat"
    state.mkdir()
    record = development_ui.DevelopmentUI(
        backend="http://127.0.0.1:8100",
        ui="http://127.0.0.1:8200",
        owner_pid=os.getpid(),
        owner_created=psutil.Process().create_time(),
        frontend_pid=os.getpid(),
        frontend_created=psutil.Process().create_time(),
    )
    path = state / development_ui.RECORD_NAME

    def endpoint(_root: Path) -> Mock:
        return Mock(base_url="http://127.0.0.1:8100")

    monkeypatch.setattr(development_ui, "read_daemon_endpoint_record", endpoint)
    path.write_text(record.model_dump_json())
    assert development_ui.current_ui(tmp_path) == record.ui
    for changes in (
        {"backend": "http://127.0.0.1:8101"},
        {"owner_created": 0},
        {"frontend_created": 0},
        {"ui": record.backend},
        {"ui": "https://example.com"},
        {"ui": "http://127.0.0.1:8200/?redirect=other"},
    ):
        path.write_text(json.dumps({**record.model_dump(), **changes}))
        with pytest.raises(ValueError, match=r"Development|Invalid"):
            development_ui.current_ui(tmp_path)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        development_ui.current_ui(tmp_path)

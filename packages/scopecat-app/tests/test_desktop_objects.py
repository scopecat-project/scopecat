"""Real host protocol with only native rendering replaced in headless tests."""

import threading
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from scopecat.daemon.client import DaemonClient
from scopecat.daemon.health import DaemonHealth
from scopecat.records.application_open import ApplicationOpenTarget
from scopecat_app.desktop import DesktopView, DesktopWindows
from scopecat_app.desktop_objects import DesktopObjectOpener, object_location
from scopecat_app.desktop_session import DesktopSession
from scopecat_server.services.application_open import ApplicationOpenService


@pytest.mark.parametrize("page", ["http://127.0.0.1:3210", "http://localhost:5179/"])
@pytest.mark.parametrize(
    "target,query,fragment",
    [
        (
            ApplicationOpenTarget(kind="run", object_id="run &?#/测量"),
            {"run": ["run &?#/测量"]},
            "runs",
        ),
        (
            ApplicationOpenTarget(kind="procedure", object_id="p", view="decision"),
            {"procedure": ["p"]},
            "decisions",
        ),
        (
            ApplicationOpenTarget(kind="analysis", object_id="a"),
            {"analysis": ["a"]},
            "analyses",
        ),
        (
            ApplicationOpenTarget(kind="run_analysis", object_id="a", owner_id="run"),
            {"run": ["run"], "run-analysis": ["a"]},
            "runs",
        ),
        (
            ApplicationOpenTarget(
                kind="sample_analysis", object_id="a", owner_id="sample"
            ),
            {"sample": ["sample"], "sample-analysis": ["a"]},
            "samples",
        ),
    ],
)
def test_host_routes_exact_objects_at_its_own_gui_origin(page, target, query, fragment):
    location = urlsplit(object_location(page, target))
    assert location.netloc == urlsplit(page).netloc
    assert parse_qs(location.query) == query
    assert location.fragment == fragment


def test_windows_use_development_page_and_focus_exact_existing_target():
    session = DesktopSession(Mock(), threading.Event())
    session.connected("http://127.0.0.1:3210")
    session.ui_url = "http://localhost:5179"
    windows = DesktopWindows(session, Mock())
    target = ApplicationOpenTarget(kind="procedure", object_id="p")
    view = Mock()
    view.loaded.wait.return_value = True
    view.window.get_current_url.return_value = object_location(session.ui_url, target)
    windows._views.append(view)
    with patch.object(windows, "create") as create:
        windows.open_target(target)
        windows.open_target(target)
        create.assert_not_called()
    view.window.show.assert_called()


def client_for(service):
    client = Mock(spec=DaemonClient)
    client.base_url = "http://127.0.0.1:3210"
    client.health.return_value = DaemonHealth(
        status="ok",
        project_id="data",
        deployment_id="app",
        project_name="test",
        author_workspaces={},
        project_root="/unused-test-root",
        data_root="/unused-test-root",
        deployment_root="/unused-test-root",
    )
    client.register_desktop_host.side_effect = service.register
    client.poll_desktop_open.side_effect = service.poll
    client.acknowledge_desktop_open.side_effect = service.acknowledge
    client.unregister_desktop_host.side_effect = service.unregister
    return client


def test_lost_ack_does_not_repeat_native_open():
    from datetime import UTC, datetime
    from uuid import uuid4

    from scopecat.records.application_open import ApplicationOpenCommand

    service = ApplicationOpenService("data", "app", Mock())
    client = client_for(service)
    session = DesktopSession(Mock(), threading.Event())
    session.connected(client.base_url)
    render = Mock()
    with patch("scopecat_app.desktop_objects.DaemonClient", return_value=client):
        opener = DesktopObjectOpener(session, render)
        opener.pump()
        request = ApplicationOpenCommand(
            project_id="data",
            deployment_id="app",
            host_id=service.status().host_id,
            request_id=uuid4(),
            issued_at=datetime.now(UTC),
            target=ApplicationOpenTarget(kind="run", object_id="r"),
        )
        service.submit(request)
        client.acknowledge_desktop_open.side_effect = httpx2.ConnectError("lost ack")
        opener.pump()
        render.assert_called_once_with(request.target)
        client.acknowledge_desktop_open.side_effect = service.acknowledge
        opener.pump()
        opener.pump()
        render.assert_called_once()
        assert service.submit(request).state == "opened"
        opener.close()


def test_native_creation_failure_is_terminal_even_if_a_window_was_partly_created():
    from uuid import uuid4

    from scopecat.records.application_open import ApplicationOpenCommand

    service = ApplicationOpenService("data", "app", Mock())
    client = client_for(service)
    session = DesktopSession(Mock(), threading.Event())
    session.connected(client.base_url)
    render = Mock(side_effect=TimeoutError("load acknowledgement missing"))
    with patch("scopecat_app.desktop_objects.DaemonClient", return_value=client):
        opener = DesktopObjectOpener(session, render)
        opener.pump()
        request = ApplicationOpenCommand(
            project_id="data",
            deployment_id="app",
            host_id=service.status().host_id,
            request_id=uuid4(),
            target=ApplicationOpenTarget(kind="run", object_id="r"),
        )
        service.submit(request)
        opener.pump()
        opener.pump()
        assert service.submit(request).state == "failed"
        render.assert_called_once()
        opener.close()


def test_open_ack_requires_loaded_native_window_and_preserves_other_views():
    session = DesktopSession(Mock(), threading.Event())
    session.connected("http://127.0.0.1:3210")
    windows = DesktopWindows(session, Mock())
    existing = Mock()
    existing.window.get_current_url.return_value = "http://127.0.0.1:3210/#launch"
    windows._views.append(existing)
    new = Mock()
    new.loaded.wait.return_value = False
    target = ApplicationOpenTarget(kind="run", object_id="r")
    with patch.object(windows, "create", return_value=new) as create:
        with pytest.raises(TimeoutError, match="finished loading"):
            windows.open_target(target)
        create.assert_called_once_with(target=target)
    new.window.show.assert_not_called()
    existing.window.load_url.assert_not_called()


def test_run_bridge_reuses_loading_target_then_reopens_after_close():
    session = DesktopSession(Mock(), threading.Event())
    session.connected("http://127.0.0.1:3210")
    assert session.page_url is not None
    windows = DesktopWindows(session, Mock())
    target = ApplicationOpenTarget(kind="run", object_id="run-a")
    native = Mock()
    native.get_current_url.return_value = None
    view = DesktopView(
        native,
        Mock(),
        threading.Event(),
        Mock(),
        object_location(session.page_url, target),
    )
    windows._views.append(view)
    with patch.object(windows, "create", return_value=view) as create:
        windows.new_window("run-a")
        windows.new_window("run-a")
        create.assert_not_called()
        native.show.assert_called()
        # After loading, navigation belongs to the view. Its initial target must
        # not steal focus when it now shows another object.
        view.loaded.set()
        native.get_current_url.return_value = "http://127.0.0.1:3210/?run=other#runs"
        windows.new_window("run-a")
        create.assert_called_once_with(target=target)
        create.reset_mock()
        windows._views.remove(view)
        windows.new_window("run-a")
        create.assert_called_once_with(target=target)


def test_object_open_reuses_a_loading_run_bridge_view():
    session = DesktopSession(Mock(), threading.Event())
    session.connected("http://127.0.0.1:3210")
    assert session.page_url is not None
    windows = DesktopWindows(session, Mock())
    target = ApplicationOpenTarget(kind="run", object_id="run-a")
    native = Mock()
    native.get_current_url.return_value = None
    loaded = Mock()
    loaded.is_set.return_value = False
    loaded.wait.return_value = True
    windows._views.append(
        DesktopView(
            native,
            Mock(),
            loaded,
            Mock(),
            object_location(session.page_url, target),
        )
    )
    with patch.object(windows, "create") as create:
        windows.open_target(target)
        create.assert_not_called()
        loaded.wait.assert_called_once_with(5)
        native.show.assert_called()

"""Desktop routing requests cannot change owner or replay into a new host."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from unittest.mock import Mock
from uuid import uuid4

import psutil
import pytest
from scopecat.records.application_open import (
    ApplicationOpenCommand,
    ApplicationOpenTarget,
    DesktopHostLease,
    DesktopHostRegistration,
    DesktopOpenAcknowledgement,
)

from scopecat_server.errors import BackendNotFound
from scopecat_server.services.application_open import ApplicationOpenService


def host(service: ApplicationOpenService) -> DesktopHostLease:
    return service.register(
        DesktopHostRegistration(
            project_id="data",
            deployment_id="app",
            process_id=os.getpid(),
            process_created=psutil.Process().create_time(),
            instance=uuid4(),
        )
    )


def command(lease: DesktopHostLease, **changes: object) -> ApplicationOpenCommand:
    return ApplicationOpenCommand(
        project_id="data",
        deployment_id="app",
        host_id=lease.host_id,
        request_id=uuid4(),
        target=ApplicationOpenTarget(kind="procedure", object_id="p1"),
    ).model_copy(update=changes)


def test_registration_required_and_wrong_instance_rejected():
    service = ApplicationOpenService("data", "app", Mock())
    with pytest.raises(ValueError, match="Open this application's desktop"):
        service.status()
    lease = host(service)
    with pytest.raises(ValueError, match="another application"):
        service.submit(command(lease, project_id="other"))
    with pytest.raises(ValueError, match="changed"):
        service.submit(command(lease, host_id=uuid4()))
    assert service.poll(lease).items == ()


def test_duplicate_submission_and_lost_poll_or_ack_keep_one_request():
    validate = Mock()
    service = ApplicationOpenService("data", "app", validate)
    lease = host(service)
    request = command(lease)
    first = service.submit(request)
    assert service.submit(request) == first
    batch = service.poll(lease)
    assert service.poll(lease) == batch  # the first poll response may be lost
    assert len(batch.items) == 1 and batch.items[0].state == "opening"
    ack = DesktopOpenAcknowledgement(
        lease=lease, request_id=request.request_id, opened=True
    )
    opened = service.acknowledge(ack)
    assert service.acknowledge(ack) == opened
    assert service.submit(request) == opened
    assert service.poll(lease).items == ()
    with pytest.raises(ValueError, match="another target"):
        service.submit(
            request.model_copy(
                update={"target": ApplicationOpenTarget(kind="run", object_id="r2")}
            )
        )


def test_new_host_and_daemon_cannot_replay_old_requests():
    service = ApplicationOpenService("data", "app", Mock())
    lease = host(service)
    request = command(lease)
    service.submit(request)
    service.poll(lease)
    service.unregister(lease)
    replacement = host(service)
    with pytest.raises(ValueError, match="changed"):
        service.submit(request)
    assert service.poll(replacement).items == ()
    restarted = ApplicationOpenService("data", "app", Mock())
    host(restarted)
    with pytest.raises(ValueError, match="changed"):
        restarted.submit(request)


def test_expired_command_cannot_reopen_after_receipt_collection():
    service = ApplicationOpenService("data", "app", Mock())
    lease = host(service)
    old = command(lease, issued_at=datetime.now(UTC) - timedelta(seconds=61))
    with pytest.raises(ValueError, match="expired"):
        service.submit(old)
    assert service.poll(lease).items == ()


def test_deleted_target_between_submission_and_host_dispatch_is_not_opened():
    validate = Mock()
    service = ApplicationOpenService("data", "app", validate)
    lease = host(service)
    request = command(lease)
    service.submit(request)
    validate.side_effect = BackendNotFound("Retained task was removed")
    assert service.poll(lease).items == ()
    receipt = service.submit(request)
    assert receipt.state == "failed" and receipt.detail == "Retained task was removed"


@pytest.mark.parametrize("kind", ["run_analysis", "sample_analysis"])
def test_owned_analysis_target_requires_owner(
    kind: Literal["run_analysis", "sample_analysis"],
) -> None:
    with pytest.raises(ValueError, match="exact run or sample"):
        ApplicationOpenTarget(kind=kind, object_id="analysis")


def test_http_host_cycle_and_invalid_target(tmp_path: Path) -> None:
    import httpx2
    from fastapi.testclient import TestClient
    from scopecat.daemon.client import DaemonClient, DaemonClientError

    from scopecat_server import LocalDaemonRuntime

    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
    ):

        def send(request: httpx2.Request) -> httpx2.Response:
            response = transport.request(
                request.method,
                request.url.raw_path.decode(),
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx2.Response(response.status_code, content=response.content)

        with DaemonClient(
            "http://testserver", transport=httpx2.MockTransport(send)
        ) as client:
            health = client.health()
            lease = client.register_desktop_host(
                DesktopHostRegistration(
                    project_id=health.project_id,
                    deployment_id=health.deployment_id,
                    process_id=os.getpid(),
                    process_created=psutil.Process().create_time(),
                    instance=uuid4(),
                )
            )
            assert client.application_open_host().host_id == lease.host_id
            request = ApplicationOpenCommand(
                project_id=health.project_id,
                deployment_id=health.deployment_id,
                host_id=lease.host_id,
                request_id=uuid4(),
                target=ApplicationOpenTarget(kind="run", object_id="missing"),
            )
            with pytest.raises(DaemonClientError):
                client.open_application_object(request)
            assert client.poll_desktop_open(lease).items == ()
            runtime.application.application_open.validate = Mock()
            assert client.open_application_object(request).state == "pending"
            assert client.poll_desktop_open(lease).items[0].target == request.target
            client.acknowledge_desktop_open(
                DesktopOpenAcknowledgement(
                    lease=lease,
                    request_id=request.request_id,
                    opened=True,
                )
            )
            assert client.open_application_object(request).state == "opened"
            client.unregister_desktop_host(lease)
            with pytest.raises(DaemonClientError, match="desktop"):
                client.application_open_host()

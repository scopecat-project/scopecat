"""Real TCP framing without a wall-clock performance threshold."""

import socket
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest

from scopecat_server.instruments.worker_transport import ByteConnection


@pytest.fixture
def connections() -> Iterator[tuple[ByteConnection, ByteConnection]]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        with (
            socket.create_connection(listener.getsockname(), timeout=5) as outgoing,
            listener.accept()[0] as incoming,
        ):
            incoming.settimeout(5)
            yield ByteConnection(outgoing), ByteConnection(incoming)


def test_frame_parts_are_not_delayed_on_either_tcp_peer(
    connections: tuple[ByteConnection, ByteConnection],
) -> None:
    for connection in connections:
        assert connection.connection.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)


def test_tcp_frames_preserve_empty_small_and_large_binary_messages(
    connections: tuple[ByteConnection, ByteConnection],
) -> None:
    client, server = connections
    payload = bytes(range(256)) * 8192

    def echo() -> None:
        assert server.recv_bytes() == b""
        assert server.recv_bytes() == b"request"
        assert server.recv_bytes() == payload
        server.send_bytes(b"response")
        server.send_bytes(memoryview(payload))

    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(echo)
        client.send_bytes(b"")
        client.send_bytes(b"request")
        client.send_bytes(memoryview(payload))
        assert client.recv_bytes() == b"response"
        assert client.recv_bytes() == payload
        response.result(timeout=5)

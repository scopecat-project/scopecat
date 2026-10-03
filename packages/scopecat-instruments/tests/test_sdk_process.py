"""Real vendor processes have independent imports and explicit failure outcomes."""

import io
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import numpy as np
import psutil
import pytest
from numpy.typing import NDArray

from scopecat_instruments import sdk_wire
from scopecat_instruments.sdk_process import SDKProcess, SDKProcessError


@pytest.fixture
def command(tmp_path: Path) -> tuple[str, ...]:
    script = tmp_path / "vendor.py"
    script.write_text(
        "import importlib.util, os, threading\n"
        "spec = importlib.util.spec_from_file_location(\n"
        f"    'wire', {sdk_wire.__file__!r})\n"
        "wire = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(wire)\n"
        "event = threading.Event()\n"
        "def echo(value): return value\n"
        "def fail(): raise RuntimeError('vendor failure')\n"
        "def wait(): event.wait(); return 'cancelled'\n"
        "def abort(): event.set()\n"
        "def crash(): os._exit(3)\n"
        "wire.serve(dict(echo=echo, fail=fail, wait=wait, abort=abort, crash=crash),\n"
        "           {'trigger_start': 7})\n"
    )
    return (sys.executable, "-I", str(script))


def test_sdk_binary_arrays_cancellation_and_owned_exit(
    command: tuple[str, ...],
) -> None:
    sdk = SDKProcess(command)
    try:
        assert sdk.constants == {"trigger_start": 7}
        values = np.arange(1024 * 1024, dtype=np.float64)[::2]
        result = cast(
            "dict[str, NDArray[np.float64]]", sdk.call("echo", value={"iq": values})
        )
        np.testing.assert_array_equal(result["iq"], values)
        with ThreadPoolExecutor() as executor:
            waiting = executor.submit(sdk.call, "wait")
            sdk.call("abort")
            assert waiting.result(timeout=3) == "cancelled"
        with pytest.raises(SDKProcessError, match="vendor failure"):
            sdk.call("fail")
        assert sdk.call("echo", value="still alive") == "still alive"
    finally:
        sdk.close()
    assert not psutil.pid_exists(sdk.pid)


@pytest.mark.parametrize("method", ["wait", "crash"])
def test_lost_sdk_does_not_retry(command: tuple[str, ...], method: str) -> None:
    sdk = SDKProcess(command)
    sdk.timeout = 2
    try:
        with pytest.raises(SDKProcessError):
            sdk.call(method)
        with pytest.raises(SDKProcessError):
            sdk.call("echo", value="no replay")
    finally:
        sdk.close()
    assert not psutil.pid_exists(sdk.pid)


def test_wire_rejects_object_arrays_and_bad_lengths() -> None:
    with pytest.raises(ValueError, match="numeric"):
        sdk_wire.send(io.BytesIO(), np.array([object()], dtype=object))
    with pytest.raises(ValueError, match="header"):
        sdk_wire.receive(io.BytesIO(b"\xff\xff\xff\xff"))


def test_upload_timeout_reaps_a_vendor_that_stops_reading(
    command: tuple[str, ...],
) -> None:
    script = Path(command[-1])
    source = script.read_text().split("wire.serve(")[0]
    script.write_text(
        source + "import sys\n"
        "wire.send(sys.stdout.buffer, dict(version=1, methods=['echo'],\n"
        "    constants={}, python=sys.version))\n"
        "event.wait()\n"
    )
    sdk = SDKProcess(command)
    sdk.timeout = 0.5
    try:
        with pytest.raises(SDKProcessError, match="timed out"):
            sdk.call("echo", value=np.ones(1024 * 1024))
    finally:
        sdk.close()
    assert not psutil.pid_exists(sdk.pid)

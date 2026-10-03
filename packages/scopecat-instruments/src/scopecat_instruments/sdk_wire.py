"""SDK bridge framing; this file also runs standalone on Python 3.10+.

Only JSON values and contiguous numeric NumPy arrays cross this boundary. No
pickle, imports, attribute traversal or device ownership commands are accepted.
stdout belongs to the protocol; vendor logging must use stderr.
"""

from __future__ import annotations

import json
import os
import struct
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, Timer
from typing import BinaryIO, cast

import numpy as np
from numpy.typing import NDArray

VERSION = 1
MAX_HEADER = 1024 * 1024
MAX_DATA = 512 * 1024 * 1024


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("SDK message must be a mapping")
    result = cast("dict[object, object]", value)
    if any(not isinstance(key, str) for key in result):
        raise ValueError("SDK mappings require string keys")
    return cast("dict[str, object]", result)


def sizes_list(value: object, limit: int) -> list[int]:
    if not isinstance(value, list):
        raise ValueError("SDK sizes must be a list")
    items = cast("list[object]", value)
    if len(items) > limit or any(type(n) is not int or n < 0 for n in items):
        raise ValueError("Invalid SDK sizes")
    return cast("list[int]", items)


def _read(stream: BinaryIO, size: int) -> bytes:
    parts = bytearray()
    while len(parts) < size:
        part = stream.read(size - len(parts))
        if not part:
            raise EOFError("SDK bridge closed its output")
        parts.extend(part)
    return bytes(parts)


def send(stream: BinaryIO, value: object) -> None:
    buffers: list[bytes] = []
    total = 0

    def encode(item: object) -> object:
        nonlocal total
        if isinstance(item, np.ndarray):
            numeric = cast("NDArray[np.generic]", item)
            if numeric.dtype.kind not in "biufc" or numeric.nbytes > MAX_DATA:
                raise ValueError("SDK arrays must have numeric dtypes")
            total += numeric.nbytes
            if total > MAX_DATA or len(buffers) >= 4096:
                raise ValueError("SDK message exceeds transfer limits")
            array = np.ascontiguousarray(numeric)
            index = len(buffers)
            buffers.append(array.tobytes())
            return {
                "$array": index,
                "dtype": array.dtype.str,
                "shape": list(array.shape),
            }
        if isinstance(item, np.generic):
            return encode(item.item())
        if isinstance(item, (tuple, list)):
            return [
                encode(child)
                for child in cast("tuple[object, ...] | list[object]", item)
            ]
        if isinstance(item, dict):
            values = mapping(cast("object", item))
            if "$array" in values:
                raise ValueError("SDK mappings need string keys and cannot use $array")
            return {key: encode(child) for key, child in values.items()}
        if item is None or isinstance(item, (str, bool, int, float)):
            return item
        raise TypeError(f"Unsupported SDK value: {type(item).__name__}")

    payload = encode(value)
    sizes = [len(item) for item in buffers]
    header = json.dumps({"value": payload, "sizes": sizes}, allow_nan=False).encode()
    if len(header) > MAX_HEADER or sum(sizes) > MAX_DATA or len(sizes) > 4096:
        raise ValueError("SDK message exceeds transfer limits")
    for buffer in (struct.pack("!I", len(header)), header, *buffers):
        view = memoryview(buffer)
        while view:
            written = stream.write(view)
            if not written:
                raise BrokenPipeError("SDK bridge input closed")
            view = view[written:]
    stream.flush()


def receive(stream: BinaryIO) -> object:
    size = int.from_bytes(_read(stream, 4), "big")
    if size > MAX_HEADER:
        raise ValueError("SDK header exceeds transfer limit")
    header = mapping(cast("object", json.loads(_read(stream, size))))
    sizes = sizes_list(header["sizes"], 4096)
    if sum(sizes) > MAX_DATA:
        raise ValueError("Invalid SDK attachment sizes")
    buffers = [_read(stream, size) for size in sizes]

    def decode(item: object) -> object:
        if isinstance(item, list):
            return [decode(child) for child in cast("list[object]", item)]
        if isinstance(item, dict):
            values = mapping(cast("object", item))
            if "$array" in values:
                descriptor = values["dtype"]
                if not isinstance(descriptor, str):
                    raise ValueError("Invalid SDK dtype")
                dtype = np.dtype(descriptor)
                shape = sizes_list(values["shape"], 16)
                index = values["$array"]
                if (
                    dtype.kind not in "biufc"
                    or type(index) is not int
                    or not 0 <= index < len(buffers)
                ):
                    raise ValueError("Invalid SDK array descriptor")
                return np.frombuffer(buffers[index], dtype=dtype).reshape(shape).copy()
            return {key: decode(child) for key, child in values.items()}
        return item

    return decode(header["value"])


def serve(
    methods: dict[str, Callable[..., object]],
    constants: dict[str, object],
    *,
    output: BinaryIO | None = None,
) -> None:
    """Serve an explicit SDK surface; stop methods may run beside a blocked call.

    The adapter owns connection acquisition, final device stop and release.
    EOF is a request to leave this function and execute the adapter's finally.
    """
    outgoing: BinaryIO = output if output is not None else sys.stdout.buffer
    incoming = sys.stdin.buffer
    sys.stdout = sys.stderr
    lock = Lock()
    send(
        outgoing,
        {
            "version": VERSION,
            "methods": sorted(methods),
            "constants": constants,
            "python": sys.version,
        },
    )

    def invoke(request: dict[str, object]) -> None:
        identity = request["id"]
        try:
            result = methods[cast("str", request["method"])](
                **mapping(request["kwargs"])
            )
            response = {"id": identity, "result": result}
        except Exception as error:
            response = {"id": identity, "error": f"{type(error).__name__}: {error}"}
        with lock:
            try:
                send(outgoing, response)
            except (TypeError, ValueError) as error:
                send(outgoing, {"id": identity, "error": str(error)})

    with ThreadPoolExecutor(max_workers=4) as executor:
        while True:
            try:
                request = mapping(receive(incoming))
            except EOFError:
                # The owning driver may have crashed while a vendor call hangs.
                # EOF also requests normal release: allow finally blocks to run,
                # but never leave a parentless blocked SDK process indefinitely.
                watchdog = Timer(5, lambda: os._exit(125))
                watchdog.daemon = True
                watchdog.start()
                break
            if request.get("version") != VERSION:
                raise ValueError("Incompatible SDK request")
            if (
                type(request.get("id")) is not int
                or not isinstance(request.get("method"), str)
                or request.get("method") not in methods
                or not isinstance(request.get("kwargs"), dict)
            ):
                raise ValueError("Invalid SDK call")
            executor.submit(invoke, request)

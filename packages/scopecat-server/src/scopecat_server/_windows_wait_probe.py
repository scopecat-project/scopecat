"""Temporary query-only owned-thread I/O probe; no debug privilege or system trace."""

from __future__ import annotations

import ctypes as C
import os
import psutil


def snapshot(pid: int, created: float) -> dict:
    if os.name != "nt":
        return {"unsupported": os.name}
    process = psutil.Process(pid)
    if process.create_time() != created:
        return {"identity_changed": True}
    kernel = C.WinDLL("kernel32", use_last_error=True)
    dword, boolean, handle_type = C.c_uint32, C.c_int32, C.c_void_p
    kernel.OpenThread.argtypes = [dword, boolean, dword]
    kernel.OpenThread.restype = handle_type
    kernel.CloseHandle.argtypes = [handle_type]
    kernel.CloseHandle.restype = boolean
    kernel.GetProcessIdOfThread.argtypes = [handle_type]
    kernel.GetProcessIdOfThread.restype = dword
    kernel.GetThreadIOPendingFlag.argtypes = [handle_type, C.POINTER(boolean)]
    kernel.GetThreadIOPendingFlag.restype = boolean
    result = {"threads": [], "wait_chain": "not_requested_requires_debug_privilege"}
    for thread in process.threads():
        row = {
            "tid": thread.id,
            "user_time": thread.user_time,
            "system_time": thread.system_time,
        }
        handle = kernel.OpenThread(0x0040, False, thread.id)
        if not handle:
            row["open_error"] = C.get_last_error()
            result["threads"].append(row)
            continue
        try:
            owner = kernel.GetProcessIdOfThread(handle)
            if not owner:
                row["owner_error"] = C.get_last_error()
            elif owner != pid:
                row["identity_changed"] = True
            else:
                pending = boolean()
                if kernel.GetThreadIOPendingFlag(handle, C.byref(pending)):
                    row["io_pending"] = bool(pending.value)
                else:
                    row["io_error"] = C.get_last_error()
        finally:
            kernel.CloseHandle(handle)
        result["threads"].append(row)
    if psutil.Process(pid).create_time() != created:
        return {"identity_changed": True}
    return result


if __name__ == "__main__":
    import json

    print(json.dumps(snapshot(os.getpid(), psutil.Process().create_time())))

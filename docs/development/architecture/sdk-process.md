# Independent SDK processes

The application owns devices and tasks. A driver worker runs in the selected
execution Python; it may own an additional vendor process when the SDK requires a
different Python, NumPy, native runtime or operating system. This is a local call
boundary, not another service registry or device authority. Browsing metadata
must not launch the SDK or connect equipment.

`scopecat_instruments.sdk_process.SDKProcess` launches an explicit command without
a shell and clears inherited Python environment overrides. The vendor entry loads
`sdk_wire.py` by filename; this standalone module requires only Python 3.10+ and
NumPy. It does not import Scopecat. A native program may implement the same framing.
Private MMCS provides a concrete entry and selects it with `mmcs.sdk_python` in
local settings. Without that setting it uses the driver environment directly.

Version 1 uses stdin/stdout binary framing: a four-byte big-endian JSON header
length, the UTF-8 header, then the declared raw buffers. The header contains
`value` and `sizes`. Numeric arrays inside value use
`{"$array": index, "dtype": numpy_dtype_string, "shape": dimensions}`; arrays
are contiguous, uncompressed and never pickled. Limits are 1 MiB of JSON,
512 MiB of array data, 4096 attachments and 16 dimensions per array. Empty arrays
are valid. Structured/object dtypes and arbitrary Python objects are rejected.

The first response advertises `version`, allowed `methods`, JSON `constants` and
the interpreter `python` identity. Each request has `version`, integer `id`,
`method` and keyword `kwargs`; a response has matching `id` and either `result` or
`error`. Unadvertised methods, malformed framing and incompatible versions fail
explicitly. stdout is reserved for the protocol; vendor output goes to stderr.
The host retains the last 8 KiB of diagnostics when reporting transport failures.

Calls and uploads are bounded. The small concurrent dispatch pool allows an
explicit driver stop method to run while acquisition waits; it is not a guarantee
that an arbitrary vendor SDK is thread-safe. Each adapter chooses its allowed
surface and cancellation operation. EOF asks the vendor entry to stop equipment
and release connections; after a grace period the host terminates owned processes.
Driver shutdown also reaps nested vendor processes when the driver itself fails.
On stdin EOF, the standalone SDK entry allows five seconds for release before
exiting even if a vendor thread is blocked, including when its owning driver crashes.
Process identities are captured before signaling; no name-based kill is used.

A timeout, lost connection or failed final stop never means physical output is
safe. No request is replayed automatically. The existing device runtime retains
attention/uncertainty and requires explicit recovery and a fresh connection.
Killing software cannot establish analog zero or replace physical verification.
Windows-only SDKs still require Windows.

Tests cover binary arrays, concurrent cancellation, failure without retry, cleanup,
and interpreter selection across driver restart. The MMCS consumer additionally
tests Python 3.12/NumPy 1.26.4 against the current execution environment. Those are
software surrogate results; real vendor and physical-device acceptance stays separate.

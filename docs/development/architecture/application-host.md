# Local application host

The default teaching installation has one management service per installation home,
with a browser page and CLI using the same authenticated API. The first delivered
slice manages synthetic teaching exercises. Existing project daemons, kernels,
source revisions and scientific stores retain their execution semantics.

## Ownership

The host owns discovery, the teaching inventory and serialized lifecycle operations.
The managed directory UUID identifies a teaching workspace; API requests use that
identity rather than accepting arbitrary filesystem paths. Existing managed copies
are discovered from their teaching metadata, version and current-generation record.
All mutation paths revalidate membership and reject symlink escape. Current copies
and copies with running processes are protected from deletion. Real experiment
workspaces and data spaces are not admitted to this disposable inventory.

Each exercise retains its installed Python environment and project daemon. The host
never imports its author package or acquires its devices. This preserves dependency,
module-name, source-revision, data-writer and failure boundaries during the migration.
A host restart does not stop exercise services, rewrite retained data or redirect
Notebook requests. This is one application entry with managed child services, not
a claim that every execution now shares one Python process.

## Process and operation lifecycle

A launch lock serializes concurrent clients; a separate owner lock protects the
host lifetime. A private endpoint record carries process creation time, instance
identity, protocol, runtime content identity and a random token. The host listens
only on loopback, validates Host/Origin and requires the token for API access.
The browser receives it in a URL fragment and removes the fragment immediately;
API replies and logs do not expose it. This is a trusted local-user service, not
an isolation boundary against arbitrary code running as that same OS user.

Operations persist in `host/operations.sqlite3`; output is retained per operation.
Client-generated IDs make resubmission idempotent. Only one management operation
runs at a time, and admission/claim transitions use SQLite writer transactions.
A short-lived worker runs in the manager's installed environment and uses each
exercise's interpreter for execution. Its process identity and completion are
persisted independently of the HTTP process. After restart, live workers remain
observable; absent workers become interrupted, with files and logs preserved.
Interrupted work is never replayed automatically. Creating a new copy publishes
the current pointer only after its environment is ready.

Graceful host shutdown and runtime replacement reject active management work.
Replacing the manager does not stop an already running exercise. Fixed releases
live in separate installed directories; source-development entry stops its manager
before replacing the source runtime. UI and CLI both use the operation API, so
there is one lifecycle owner rather than two competing filesystem implementations.

## Development and acceptance

Tests use explicit temporary homes and dynamic loopback ports. They do not install
an OS service or depend on the user's default installation. Real-process tests
cover concurrent launch, identity reuse, authenticated access, interrupted HTTP
ownership and operation reconnection. Installed Windows/Linux acceptance creates
all four topics through the same host, runs the shipped Notebooks, resets a copy,
deletes the selected old copy and shuts down the host.

## Further deployment decisions

The existing workspace/data-space/deployment binding remains authoritative for
project execution. Before generalizing this host to laboratories, define stable
workspace and deployment IDs independent of paths, cross-workspace physical-device
ownership, session authorization and compatible runtime upgrade rules. Independent
stores currently do not establish exclusion for two declarations targeting the same
physical instrument. A management wrapper does not solve that problem by itself.

Tray integration and login startup can be added to this entry after its lifecycle
has been exercised. Unattended machine services need an explicit OS identity and
device-access policy. LAN access needs authenticated clients and distinct rights for
reading results, publishing source, changing parameters and controlling devices.
These features are not enabled by this initial local teaching implementation.

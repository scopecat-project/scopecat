---
default: minor
---

# Persistent author application discovery

Registered author folders use per-user application locators instead of four-path
runtime files. Independent Notebook kernels verify the existing scientific data
identity and author registration before connecting. Interrupted registration,
ambiguous targets, missing data and active independent runtimes fail closed;
legacy bindings and scientific data are retained without implicit migration.

Default targets are client preferences. Authors can register with multiple explicit
data spaces without changing their default. Notebook sessions pin store, source
and service lifetime; stale sessions and cross-space job reconnects are rejected.

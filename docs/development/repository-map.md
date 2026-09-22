# Repository map

| Path | Responsibility |
| --- | --- |
| `packages/scopecat` | Domain-neutral authoring, planning, execution, measurement, and notebook APIs. |
| `packages/scopecat-server` | Project CLI, local HTTP/SSE daemon, services, and storage. |
| `packages/scopecat-instruments` | Typed instrument capabilities, drivers, transports, and virtual devices. |
| `packages/scopecat-quantum` | Hardware-independent quantum building blocks and target contracts. |
| `apps/scopecat-ui` | React/Vite project console. |
| `examples/reference_lab` | Retained device/scientific integration fixtures; legacy gallery under retirement. |
| `testing/scopecat-testkit` | Shared test support with explicit package boundaries. |
| `fixtures` | Test-only serialized inputs. |
| `docs` | Published user, extension, reference, and contributor documentation. |

The root workspace owns cross-package checks and dependency locking. Each
publishable Python package owns its build metadata and focused tests. The UI
owns its Node dependency graph and browser tests.

See [execution architecture](architecture/execution.md) for lowering and effect
boundaries and [daemon architecture](architecture/daemon.md) for durable
ownership and client separation.

# Configuration ownership convergence

Tracking: [#754](https://github.com/scopecat-project/scopecat/issues/754).

The maintained contract and the remaining implementation boundary are recorded in
[configuration ownership and execution fences](architecture/configuration-ownership.md).
Device registration, setup definitions and parameter branches are separate owners.
Resolved configurations are execution evidence rather than another editing API.

Global-default publication/activation, combined working-point writes, setup
rebind and intermediate parameter binding saves have retired with their mutable
storage, API and obsolete consumers. Ordinary preparation uses independent
parameter/setup inputs. Named combined snapshots remain exact execution/evidence
data; they are not an alternative editor or a default-selection mechanism.

User workflows are described in [parameter editing](../how-to/manage-configuration.md),
[experiment setup](../how-to/maintain-executable-setup.md) and
[verified publication](../how-to/publish-working-point-calibration.md).

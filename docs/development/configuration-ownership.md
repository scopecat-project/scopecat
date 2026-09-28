# Configuration ownership convergence

Tracking: [#754](https://github.com/scopecat-project/scopecat/issues/754).

The maintained contract and the remaining implementation boundary are recorded in
[configuration ownership and execution fences](architecture/configuration-ownership.md).
Device registration, setup definitions and parameter branches are separate owners.
Resolved configurations are execution evidence rather than another editing API.

The current batch retires working-point editors and global-default publication
commands together with their GUI and Python consumers. Remaining
combined-configuration execution and bootstrap dependencies must also be accounted
for before the batch is complete. Do not retain a public writer solely for old
development fixtures or add prebaseline compatibility readers.

User workflows are described in [parameter editing](../how-to/manage-configuration.md),
[experiment setup](../how-to/maintain-executable-setup.md) and
[verified publication](../how-to/publish-working-point-calibration.md).

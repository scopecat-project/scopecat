---
default: major
---

The complete application's `scopecat` command is now provided by
`scopecat-lab-tools`. Server-only installations use
`python -m scopecat_server.cli COMMAND` instead of `scopecat COMMAND`.
Application and teaching commands previously invoked through the server module
use `scopecat` or `python -m lab_tools.public_cli` instead. Complete application
console commands are unchanged; retained project data needs no migration.

---
default: major
---

Separate application and server command entries

Server-only installations use `python -m scopecat_server.cli COMMAND` instead
of `scopecat COMMAND`. Complete application console commands remain unchanged.
Application and teaching commands previously invoked through the server module
use `scopecat` instead. Retained project data needs no migration.

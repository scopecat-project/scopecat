---
default: patch
---

Give Windows desktop windows their own Scopecat application identity.

The installed desktop now hosts Python inside Scopecat.exe, avoiding confusion
with unrelated Python applications in native automation tools. Windows source
development uses a cached Scopecat Dev.exe with a separate worktree identity,
while preserving the editable Python environment and Vite hot reload. Building
the development host requires Visual Studio C++ Build Tools and the Windows SDK.

---
default: patch
---

Keep the packaged Mac maintenance command usable after installation

The Mac application's `Contents/Resources/python/bin/scopecat` command now finds
Python inside the same application after it is moved or installed, including in
paths with spaces or Chinese characters. It no longer depends on a temporary
build directory. The launcher is prepared before signing and does not modify the
application when run.

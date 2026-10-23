---
default: patch
---

# Keep the desktop usable without a tray

Optional tray failures no longer block desktop startup. When a usable native tray
entry cannot be confirmed, keep the last window accessible and explain the mode
through the File menu. Explicit Quit retains the existing saved-input and work
drain decisions. Linux conservatively uses this mode without importing pystray.

---
default: minor
---

# 按已查看的问题提交人工判断

普通作者会话新增 `task.pending_input(step_key, schema=...)`。取得问题和证据后，
通过 `pending.respond(answer, actor=...)` 提交类型化答案，无需手填并发字段。
答复绑定原问题；问题变化时拒绝覆盖，不自动刷新重发或继续执行。

"use strict";
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has("token")) {
  sessionStorage.setItem("scopecat-token", fragment.get("token"));
  history.replaceState(null, "", location.pathname);
}
const token = sessionStorage.getItem("scopecat-token") || "";
const notice = document.getElementById("notice");
let busy = false, stopped = false, polling;
let renderedState = "";
function message(text, error = false) {
  notice.textContent = text;
  notice.classList.toggle("error", error);
}
async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "请求无效，请刷新页面后重试");
  return data;
}
function element(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function button(text, action, cls = "secondary", disabled = false) {
  const node = element("button", text, cls);
  node.disabled = disabled;
  node.addEventListener("click", () => Promise.resolve().then(action).catch(error => message(error.message, true)));
  return node;
}
async function openEditor(id) {
  const result = await api(`/api/editor/${id}`, {});
  message(result.detail);
}
async function submit(command) {
  if (busy) return;
  busy = true;
  command.id = crypto.randomUUID().replaceAll("-", "");
  message("正在处理，请稍候。你可以关闭页面，稍后回来查看结果。");
  try {
    let operation = await api("/api/operations", command);
    await refresh();
    while (["starting", "running"].includes(operation.status)) {
      await new Promise(resolve => setTimeout(resolve, 1200));
      operation = await api(`/api/operations/${command.id}`);
    }
    if (operation.status !== "succeeded") throw new Error(operation.detail);
    message("已完成。");
    if (command.action === "open" && operation.workspace) await openEditor(operation.workspace);
  } finally {
    busy = false;
    await refresh();
  }
}
async function refresh() {
  if (stopped) return;
  const state = await api("/api/state");
  if (stopped) return;
  const running = state.operations.some(op => ["starting", "running"].includes(op.status));
  const disabled = busy || running;
  const signature = JSON.stringify([state, disabled]);
  if (signature === renderedState) return;
  renderedState = signature;
  const topics = document.getElementById("topics");
  topics.replaceChildren();
  for (const [topic, title] of Object.entries(state.topics)) {
    const card = element("article", undefined, "card");
    card.append(element("h3", title));
    card.append(button("打开 / 继续", () => submit({ action: "open", topic }), "primary", disabled));
    topics.append(card);
  }
  const workspaces = document.getElementById("workspaces");
  workspaces.replaceChildren();
  if (!state.workspaces.length) workspaces.append(element("p", "还没有练习。选择上面的任一专题即可开始。"));
  for (const workspace of state.workspaces) {
    const row = element("article", undefined, "row");
    const label = workspace.active_version && workspace.current ? "当前练习" : "旧副本";
    row.append(element("strong", `${workspace.title} · ${label}`));
    row.append(element("div", `${workspace.in_use ? "有进程使用中" : "未在运行"} · ${workspace.id.slice(0, 8)}`, "meta"));
    const actions = element("div", undefined, "actions");
    actions.append(button("在 VS Code 打开文件", () => openEditor(workspace.id), "secondary", disabled));
    actions.append(button("停止练习服务", () => submit({ action: "stop", workspace: workspace.id }), "secondary", disabled));
    if (workspace.active_version && workspace.current) {
      actions.append(button("重置练习", () => {
        if (confirm("创建新的练习副本？原副本保留。请先关闭 Notebook 内核并停止该练习服务。"))
          return submit({ action: "open", topic: workspace.topic, reset: true });
      }, "secondary", disabled));
      actions.append(button("自动验收", () => submit({ action: "verify", topic: workspace.topic }), "secondary", disabled || workspace.in_use));
    }
    actions.append(button("删除旧副本", () => {
      if (confirm(`永久删除这个旧练习及其中的修改？\n${workspace.root}\n此操作不可撤销。`))
        return submit({ action: "delete", workspace: workspace.id });
    }, "danger", disabled || !workspace.deletable));
    row.append(actions);
    const details = element("details");
    details.append(element("summary", "文件位置"), element("p", workspace.root, "meta"));
    row.append(details);
    workspaces.append(row);
  }
  const operations = document.getElementById("operations");
  operations.replaceChildren();
  const states = { starting: "准备中", running: "进行中", succeeded: "已完成", failed: "失败", interrupted: "已中断" };
  const actions = { open: "打开练习", verify: "自动验收", stop: "停止服务", delete: "删除旧副本" };
  for (const operation of state.operations.slice(0, 12)) {
    const row = element("article", undefined, "row");
    const topic = operation.command.topic || state.workspaces.find(item => item.id === operation.command.workspace)?.topic;
    const target = state.topics[topic] || operation.command.workspace?.slice(0, 8) || "";
    row.append(element("strong", `${target} · ${actions[operation.command.action]} · ${states[operation.status]}`));
    row.append(element("p", operation.detail));
    row.append(button("查看日志", async () => {
      const log = await api(`/api/operations/${operation.command.id}/log`);
      document.getElementById("log").textContent = log.text;
      document.getElementById("log-panel").open = true;
    }));
    operations.append(row);
  }
  document.getElementById("shutdown").disabled = disabled;
}
document.getElementById("shutdown").addEventListener("click", async () => {
  try {
    const result = await api("/api/shutdown", {});
    stopped = true;
    clearInterval(polling);
    document.querySelectorAll("button").forEach(button => { button.disabled = true; });
    message(result.detail + "。再次打开 Scopecat 安装入口可启动管理服务。");
  } catch (error) { message(error.message, true); }
});
refresh().then(() => message("已连接本机 Scopecat。选择专题开始练习。"))
  .catch(error => message(error.message, true));
polling = setInterval(() => refresh().catch(error => message(`连接暂不可用：${error.message}。请从 Scopecat 安装入口重新打开。`, true)), 2500);

"use strict";
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has("token")) {
  sessionStorage.setItem("scopecat-token", fragment.get("token"));
  history.replaceState(null, "", location.pathname);
  if (fragment.get("view") === "help") document.getElementById("help").open = true;
}
const token = sessionStorage.getItem("scopecat-token") || "";
const notice = document.getElementById("notice");
let busy = false, stopped = false, polling;
let renderedState = "";
let setupInitialized = false, setupVisited = false, managerRunning = false;
const setupForm = document.getElementById("setup-form");
const setupPanel = document.getElementById("setup-panel");
const setupFields = document.getElementById("setup-fields");
const readyServices = new Map();
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
function workbenchUrl(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) ||
      !url.port || url.username || url.password || url.search || url.hash || url.pathname !== "/")
    throw new Error("工作台地址无效，请重新检查实验服务。");
  return url.href;
}
function forgetReadyLinks() {
  readyServices.clear();
  document.querySelectorAll("[data-workbench-link]").forEach(link => link.remove());
}
async function submit(command) {
  if (busy || managerRunning) return;
  busy = true;
  setupFields.disabled = true;
  if (command.service) {
    readyServices.delete(command.service);
    document.querySelectorAll("[data-workbench-link]").forEach(link => {
      if (link.dataset.workbenchLink === command.service) link.remove();
    });
  }
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
    message(operation.detail || "已完成。");
    if (["service_start", "setup"].includes(command.action)) {
      const state = await api("/api/state");
      const identity = command.action === "setup" ? operation.service : command.service;
      const service = state.services.find(item => item.service.id === identity);
      if (service?.state !== "running" || !service.url) throw new Error("实验服务尚未就绪，请查看日志。");
      const url = workbenchUrl(service.url);
      if (command.action === "setup") {
        location.assign(url);
        return;
      }
      readyServices.set(identity, url);
      message("实验服务已就绪。点击“打开工作台（新标签页）”；本管理页面会保留，方便返回帮助、教学和服务管理。");
    }
    if (command.action === "open" && operation.workspace) await openEditor(operation.workspace);
  } finally {
    busy = false;
    await refresh();
  }
}
async function refresh() {
  if (stopped) return;
  let state;
  try { state = await api("/api/state"); }
  catch (error) { forgetReadyLinks(); throw error; }
  if (stopped) return;
  for (const [identity, url] of readyServices) {
    const service = state.services.find(item => item.service.id === identity);
    if (service?.state !== "running" || !service.url || service.url.replace(/\/$/, "") !== url.replace(/\/$/, "")) readyServices.delete(identity);
  }
  const running = state.operations.some(op => ["starting", "running"].includes(op.status));
  managerRunning = running;
  const disabled = busy || running;
  setupFields.disabled = disabled;
  if (!setupInitialized) {
    setupInitialized = true;
    setupPanel.open = !state.services.length;
    document.getElementById("setup-title").textContent = state.services.length ? "接入另一个实验工作台" : "设置主要实验工作台";
    const project = document.getElementById("setup-project");
    if (!setupVisited && !project.value) project.value = state.setup_defaults?.project || "";
    document.getElementById("setup-data").placeholder = "留空使用项目默认目录";
  }
  const signature = JSON.stringify([state, disabled, [...readyServices]]);
  if (signature === renderedState) return;
  renderedState = signature;
  const services = document.getElementById("services");
  services.replaceChildren();
  if (!state.services.length) services.append(element("p", "尚未接入实验服务。使用上方表单创建项目或连接实验室的代码文件夹。"));
  const serviceStates = { running: "运行中", stopped: "未启动", stale: "记录待检查", degraded: "需要处理", unavailable: "不可用" };
  for (const item of state.services) {
    const row = element("article", undefined, "row");
    row.append(element("strong", item.service.name), element("p", serviceStates[item.state]));
    if (item.detail) row.append(element("p", item.detail));
    row.append(button("启动 / 检查工作台", () => submit({ action: "service_start", service: item.service.id }), "primary", disabled));
    const readyUrl = readyServices.get(item.service.id);
    if (readyUrl && !disabled) {
      const link = element("a", "打开工作台（新标签页）", "workbench-link");
      link.href = readyUrl;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.dataset.workbenchLink = item.service.id;
      row.append(link);
    }
    row.append(button("停止服务", () => {
      if (confirm(`停止 ${item.service.name} 的后台服务？\n这可能中断当前任务及 Notebook 连接。项目和已有科学记录保留，不会自动重启或恢复测量。`))
        return submit({ action: "service_stop", service: item.service.id });
    }, "secondary", disabled || item.state === "stopped" || item.state === "unavailable"));
    row.append(button("重新检查环境", () => {
      if (confirm(`重新检查 ${item.service.name} 已登记的环境？\n仅检查原有项目、解释器和 GUI，成功后更新最后登记的环境身份；不会安装软件或启动服务。`))
        return submit({ action: "service_recheck", service: item.service.id });
    }, "secondary", disabled || item.state !== "stopped"));
    row.append(button("移除登记", () => {
      if (confirm(`从列表移除 ${item.service.name}？\n只撤销登记，不删除项目目录、科学数据或操作日志。之后可通过本页重新连接。`))
        return submit({ action: "service_remove", service: item.service.id });
    }, "secondary", disabled || item.state !== "stopped"));
    const details = element("details");
    details.append(element("summary", "项目与环境"), element("p", "以下信息来自最后一次成功登记或重新检查，不代表当前环境已经通过检查。"));
    const identity = element("dl", undefined, "meta");
    for (const [label, value] of [
      ["项目目录", item.service.root],
      ["Python 解释器", item.service.python],
      ["GUI 目录", item.service.static_dir],
      ["本机设置登记指纹", item.service.settings_identity || "未选择"],
      ["适配包登记指纹", item.service.adapter_identity || "项目内实现"],
      ["环境前缀", item.service.environment.prefix],
      ["Python 版本", item.service.environment.python],
      ["scopecat 版本", item.service.environment.scopecat],
      ["scopecat-server 版本", item.service.environment.server],
    ]) identity.append(element("dt", label), element("dd", value));
    details.append(identity);
    row.append(details);
    services.append(row);
  }
  const topics = document.getElementById("topics");
  topics.replaceChildren();
  if (!Object.keys(state.topics).length) topics.append(element("p", "此环境未安装教学交付；实验服务可独立使用。"));
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
  const actions = { setup: "接入工作台", open: "打开练习", verify: "自动验收", stop: "停止服务", delete: "删除旧副本", service_start: "打开工作台", service_stop: "停止实验服务", service_remove: "移除登记", service_recheck: "重新检查环境" };
  for (const operation of state.operations.slice(0, 12)) {
    const row = element("article", undefined, "row");
    const topic = operation.command.topic || state.workspaces.find(item => item.id === operation.command.workspace)?.topic;
    const target = state.services.find(item => item.service.id === (operation.service || operation.command.service))?.service.name || operation.command.setup?.name || operation.command.setup?.project || state.topics[topic] || operation.command.workspace?.slice(0, 8) || operation.command.service?.slice(0, 8) || "";
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
setupForm.addEventListener("input", () => { setupVisited = true; });
document.getElementById("setup-mode").addEventListener("change", event => {
  setupVisited = true;
  const connect = event.target.value === "connect";
  document.getElementById("setup-description").textContent = connect ? "选择包含 scopecat.toml 的实验室代码文件夹。沿用已有设置；若依赖或环境需要处理，会在操作结果中说明。" : "创建可修改的基础实验项目。你的实验代码保存在此处，教学专题在帮助中单独提供。";
  document.getElementById("setup-project-help").textContent = connect ? "填写实验室代码文件夹的完整路径。" : "填写本机完整路径；填写尚不存在的新目录，应用会创建它。";
  document.getElementById("setup-data-help").textContent = connect ? "留空保留该项目已有的数据绑定。填写新目录不会迁移已有记录。" : "留空使用新项目的默认数据目录。";
  document.getElementById("setup-data").placeholder = connect ? "留空保留已有数据绑定" : "留空使用项目默认目录";
  document.getElementById("setup-submit").textContent = connect ? "连接并打开工作台" : "创建并打开工作台";
});
setupForm.addEventListener("submit", event => {
  event.preventDefault();
  const value = id => document.getElementById(id).value.trim();
  if (!value("setup-project")) { message("请填写主要代码文件夹的完整路径。", true); return; }
  submit({ action: "setup", setup: {
    mode: value("setup-mode"), project: value("setup-project"),
    data_root: value("setup-data") || null, settings_file: value("setup-settings") || null, name: value("setup-name") || null,
  } }).catch(error => message(error.message, true));
});
document.getElementById("shutdown").addEventListener("click", async () => {
  try {
    const result = await api("/api/shutdown", {});
    stopped = true;
    forgetReadyLinks();
    clearInterval(polling);
    document.querySelectorAll("button").forEach(button => { button.disabled = true; });
    message(result.detail + "。再次打开 Scopecat 安装入口可启动管理服务。");
  } catch (error) { message(error.message, true); }
});
refresh().then(() => message("已连接本机 Scopecat。接入实验代码，或打开已有工作台。"))
  .catch(error => message(error.message, true));
polling = setInterval(() => refresh().catch(error => message(`连接暂不可用：${error.message}。请从 Scopecat 安装入口重新打开。`, true)), 2500);

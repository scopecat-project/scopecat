// Shared by the native startup and recovery pages, before the workbench loads.
let pending = false;

function progress(message) {
  pending = Boolean(message);
  document.getElementById("progress").textContent = message;
  document.querySelectorAll("button").forEach((button) => { button.disabled = pending; });
}

async function operate(message, action) {
  if (pending) return;
  document.getElementById("error").textContent = "";
  progress(message);
  try {
    await action();
  } catch (error) {
    progress("");
    document.getElementById("error").textContent = error.message || String(error);
  }
}

function retry() {
  return operate("正在重新准备应用，请稍候…", () => pywebview.api.retry());
}

function resetData() {
  return operate("正在确认并准备新空间，原数据将保留…", async () => {
    const changed = await pywebview.api.reset_data();
    if (!changed) progress("");
  });
}

function restart() {
  return operate("正在停止后台并重新启动，请稍候…", () => pywebview.api.restart());
}

function exit(background) {
  return operate(background ? "正在隐藏窗口…" : "正在停止工作并释放设备，请稍候…", async () => {
    await pywebview.api.exit(background);
    progress(background ? "" : "正在关闭 Scopecat…");
  });
}

function quit() {
  return operate("正在检查未完成工作并退出，请稍候…", async () => {
    // Keep explicit stop available if the activity check itself fails.
    document.getElementById("quit-options").hidden = false;
    const work = await pywebview.api.request_exit();
    if (work) {
      progress("");
      document.getElementById("progress").textContent = "后台仍有未完成工作，请选择停止或保留后台。";
    } else {
      progress("正在关闭 Scopecat…");
    }
  });
}

window.scopecatRequestExit = quit;
window.addEventListener("keydown", (event) => {
  if (!window.pywebview || !event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || event.key.toLowerCase() !== "q") return;
  event.preventDefault();
  if (!event.repeat) quit();
});

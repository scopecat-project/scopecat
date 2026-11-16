// React must call the loader again on retry, but a browser may also cache a
// failed module. Device results offer saving and opening a separate result
// document as the fallback; never reload the controller or recollect here.
export async function loadChartRuntime() {
  const module = await import("./EChartRuntime");
  return { default: module.EChartRuntime };
}

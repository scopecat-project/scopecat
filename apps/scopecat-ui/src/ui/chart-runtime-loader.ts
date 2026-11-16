/** A new call can retry a failed network load; successful modules stay browser-cached. */
export async function loadChartRuntime() {
  const module = await import("./EChartRuntime");
  return { default: module.EChartRuntime };
}

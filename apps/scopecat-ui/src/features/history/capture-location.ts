import { navigate, useLocationUrl } from "../../lib/navigation";

export interface CaptureSelection {
  runId?: string;
  measurements: "acquired" | "selected";
  offset: number;
}

export function selectCapture(contentHash: string, selection?: CaptureSelection) {
  const url = new URL(window.location.href);
  if (url.searchParams.get("capture") !== contentHash || selection) {
    for (const key of ["capture-run", "capture-measurements", "capture-offset"])
      url.searchParams.delete(key);
  }
  url.searchParams.set("capture", contentHash);
  url.hash = "history";
  if (selection?.runId) url.searchParams.set("capture-run", selection.runId);
  if (selection?.measurements === "selected")
    url.searchParams.set("capture-measurements", "selected");
  if (selection?.offset) url.searchParams.set("capture-offset", String(selection.offset));
  navigate(url);
}

export function useCaptureSelection(contentHash: string): CaptureSelection {
  const url = useLocationUrl();
  if (url.searchParams.get("capture") !== contentHash)
    return { measurements: "acquired", offset: 0 };
  const offset = Number(url.searchParams.get("capture-offset"));
  return {
    runId: url.searchParams.get("capture-run") ?? undefined,
    measurements:
      url.searchParams.get("capture-measurements") === "selected" ? "selected" : "acquired",
    offset: Number.isSafeInteger(offset) && offset >= 0 ? offset : 0,
  };
}

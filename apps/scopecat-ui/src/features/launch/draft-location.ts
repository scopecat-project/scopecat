import { navigate } from "../../lib/navigation";
import type { LaunchDraft } from "./LaunchDraft";

export function draftLocation(
  draft: Pick<LaunchDraft, "workspaceId" | "experiment" | "draftId">,
  current = new URL(window.location.href),
): URL {
  const url = new URL(current);
  url.searchParams.set("workspace", draft.workspaceId);
  url.searchParams.set("experiment", draft.experiment);
  url.searchParams.set("draft", draft.draftId ?? "default");
  return url;
}

export function retainDraftLocation(
  draft: Pick<LaunchDraft, "workspaceId" | "experiment" | "draftId">,
) {
  navigate(draftLocation(draft), { replace: true });
}

export function clearDraftLocation(workspace?: string) {
  const url = new URL(window.location.href);
  url.searchParams.delete("draft");
  url.searchParams.delete("experiment");
  if (workspace) url.searchParams.set("workspace", workspace);
  navigate(url, { replace: true });
}

/** Same-window result navigation retains the editor identity for its return path. */
export function withDraftContext(href: string, current = new URL(window.location.href)): string {
  if (!current.searchParams.has("draft")) return href;
  const target = new URL(href, current);
  for (const key of ["workspace", "experiment", "draft"]) {
    const value = current.searchParams.get(key);
    if (value !== null && !target.searchParams.has(key)) target.searchParams.set(key, value);
  }
  return target.search + target.hash;
}

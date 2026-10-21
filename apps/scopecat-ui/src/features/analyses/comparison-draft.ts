import { useCallback, useState } from "react";

interface Draft {
  workspaceId: string;
  primary: string;
  secondary: string;
  modelId: string;
  leftPoints: string;
  rightPoints: string;
  parameters: Record<string, string>;
  selected: string;
  reason: string;
  actor: string;
}

// Only editable inputs live in browser history. Preview data stays in this
// application's query cache; a reload requires an explicit inspection, never a fit.
export function useComparisonDraft(projectId: string | undefined) {
  const [entry] = useState(() => {
    const stored = window.history.state?.scopecatComparison;
    const query = new URLSearchParams(location.search);
    const sameSpace = !stored || stored.projectId === projectId;
    const defaults: Draft = {
      workspaceId: sameSpace ? (query.get("workspace") ?? "") : "",
      primary: sameSpace ? (query.get("compare") ?? "") : "",
      secondary: "",
      modelId: "",
      leftPoints: "",
      rightPoints: "",
      parameters: {},
      selected: sameSpace ? (query.get("comparison-analysis") ?? "") : "",
      reason: "",
      actor: "",
    };
    const valid =
      stored?.projectId === projectId &&
      stored?.version === 1 &&
      typeof stored.id === "string" &&
      stored.draft &&
      Object.keys(defaults).every((key) =>
        key === "parameters"
          ? stored.draft.parameters &&
            typeof stored.draft.parameters === "object" &&
            !Array.isArray(stored.draft.parameters) &&
            Object.values(stored.draft.parameters).every((value) => typeof value === "string")
          : typeof stored.draft[key] === "string",
      );
    return {
      restored: Boolean(valid),
      id: valid ? (stored.id as string) : crypto.randomUUID(),
      draft: valid ? (stored.draft as Draft) : defaults,
    };
  });
  const [previewKey] = useState(() => ["comparison-preview", projectId, entry.id]);
  const saveDraft = useCallback(
    (draft: Draft) => {
      window.history.replaceState(
        {
          ...window.history.state,
          scopecatComparison: { version: 1, projectId, id: entry.id, draft },
        },
        "",
        window.location.href,
      );
    },
    [projectId, entry.id],
  );
  return { draft: entry.draft, restored: entry.restored, previewKey, saveDraft };
}

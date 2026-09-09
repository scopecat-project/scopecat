import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { useLaunchDraft } from "./LaunchDraft";
import { planDifferences, type PlanRevision } from "./experiment-plans";

export function PlanLibrary({ initializing = false }: { initializing?: boolean } = {}) {
  const { projectId, draft, openPlan, isCurrent } = useLaunchDraft();
  const cache = useQueryClient();
  const [history, setHistory] = useState<string>();
  const [selected, setSelected] = useState<PlanRevision>();
  const [error, setError] = useState("");
  const alive = useRef(true);
  const opening = useRef(0);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const plans = useQuery({
    queryKey: ["experiment-plans", projectId, history],
    enabled: Boolean(projectId),
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/experiment-plans", { params: { query: { plan_id: history } } }),
      ),
  });
  async function open(plan: PlanRevision) {
    const token = ++opening.current;
    const revision = draft?.revision;
    const current = () => alive.current && opening.current === token && isCurrent(revision);
    setError("");
    try {
      const catalog = await apiData(
        apiClient.GET("/api/v1/experiment-launcher", {
          params: { query: { code_revision: plan.definition.code_revision?.content_hash } },
        }),
      );
      const entry = catalog.entries.find(
        (item) =>
          item.id === plan.definition.experiment && item.version === plan.definition.version,
      );
      if (!entry)
        throw new Error(
          "The saved definition is unavailable. Its plan and history remain readable; restore its supported author revision before reopening.",
        );
      const context = plan.definition.context
        ? await apiData(
            apiClient.POST("/api/v1/config-registry/contexts/resolve", {
              body: { context: plan.definition.context, overrides: plan.definition.overrides },
            }),
          )
        : undefined;
      if (current()) openPlan(plan, entry, context);
    } catch (caught) {
      if (current()) setError(caught instanceof Error ? caught.message : String(caught));
    }
  }
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const planId = params.get("plan");
    const revision = Number(params.get("plan_revision"));
    const hash = params.get("plan_hash");
    if (!planId || !revision || !hash) return;
    void apiData(
      apiClient.POST("/api/v1/experiment-plans/read", {
        body: { plan_id: planId, revision, content_hash: hash },
      }),
    )
      .then((plan) => {
        if (!alive.current) return;
        setSelected(plan);
        setHistory(plan.ref.plan_id);
        // A durable link reveals the revision; the author explicitly opens its draft.
      })
      .catch((caught) => {
        if (alive.current) setError(String(caught));
      });
  }, []);
  async function hide(plan: PlanRevision) {
    try {
      await apiData(apiClient.POST("/api/v1/experiment-plans/hide", { body: plan.ref }));
      await cache.invalidateQueries({ queryKey: ["experiment-plans", projectId] });
    } catch (caught) {
      if (alive.current) setError(caught instanceof Error ? caught.message : String(caught));
    }
  }
  return (
    <section aria-label="Saved experiment plans" className="border rounded p-3 space-y-2">
      <h3 className="font-semibold">Saved plans</h3>
      <p>
        Saving and opening never start an experiment or change the lab default. Each run needs a
        fresh preview.
      </p>
      {initializing && <p role="status">Loading experiments before opening a saved plan…</p>}
      {history && (
        <button
          className="border border-line rounded px-2 py-1 mr-2"
          type="button"
          onClick={() => setHistory(undefined)}
        >
          Back to named plans
        </button>
      )}
      {(error || plans.error) && <p role="alert">{error || plans.error?.message}</p>}
      {plans.data?.items.length === 0 && (
        <p>No saved plans yet. Preview your inputs, then save a named plan.</p>
      )}
      {plans.data?.items.map((plan) => (
        <div
          key={`${plan.ref.plan_id}:${plan.ref.revision}`}
          className="flex flex-wrap gap-2 items-center"
        >
          <span>
            {plan.name} · revision {plan.ref.revision} · saved by {plan.saved_by}
          </span>
          <button
            className="border border-line rounded px-2 py-1 mr-2"
            type="button"
            disabled={initializing}
            onClick={() => void open(plan)}
          >
            Open {plan.name} r{plan.ref.revision}
          </button>
          <button
            className="border border-line rounded px-2 py-1 mr-2"
            type="button"
            onClick={() => {
              setSelected(plan);
              setHistory(plan.ref.plan_id);
            }}
          >
            History and compare
          </button>
          {!history && (
            <button
              className="border border-line rounded px-2 py-1 mr-2"
              type="button"
              onClick={() => void hide(plan)}
            >
              Delete {plan.name}
            </button>
          )}
          {history && (
            <button
              className="border border-line rounded px-2 py-1 mr-2"
              type="button"
              onClick={() => setSelected(plan)}
            >
              Compare revision {plan.ref.revision}
            </button>
          )}
        </div>
      ))}
      {selected && (
        <details open>
          <summary>
            {selected.name}, revision {selected.ref.revision}
          </summary>
          <p>
            Experiment: {selected.definition.experiment}. Sample:{" "}
            {selected.definition.sample
              ? `${selected.definition.sample.display_name}, revision ${selected.definition.sample.revision}`
              : "No sample"}
            .
          </p>
          {selected.definition.source && (
            <a
              className="underline"
              href={`?compare=${encodeURIComponent(selected.definition.source.run_id)}&comparison-analysis=${encodeURIComponent(selected.definition.source.analysis_id)}#analyses`}
            >
              Source analysis
            </a>
          )}
          {draft?.plan && (
            <ul aria-label="Plan revision differences">
              {planDifferences(draft.plan, selected).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
          <details>
            <summary>Exact references and inputs</summary>
            <pre className="overflow-auto text-xs">{JSON.stringify(selected, null, 2)}</pre>
          </details>
        </details>
      )}
      <p className="text-sm">
        Delete hides a named head. Existing run links and exact historical revisions remain
        readable.
      </p>
    </section>
  );
}

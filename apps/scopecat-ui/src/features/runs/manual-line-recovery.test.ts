import { afterEach, expect, it, vi } from "vitest";
import { reconfirmManualLine } from "./manual-line-recovery";

afterEach(() => vi.unstubAllGlobals());
const confirmation = {
  actor: "Alice",
  request_key: "stable-reconfirmation",
  original: {
    procedure_run_id: "parent",
    step_key: "measure/wiring",
    request_hash: `sha256:${"a".repeat(64)}`,
    runtime_id: "old-runtime",
    line_id: "line-1",
    actor: "original-operator",
  },
};

it.each(["retry", "dispatch"])(
  "recovers a lost %s response without changing confirmation or retrying another step",
  async (lost) => {
    let state = "attention_required";
    let running = false;
    let lose = true;
    const writes: { path: string; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const path = new URL(request.url).pathname;
        if (request.method === "POST") {
          const raw = await request.text();
          writes.push({ path, body: raw ? JSON.parse(raw) : undefined });
          if (path.endsWith("/retry")) state = "ready";
          if (path.endsWith("/dispatch")) {
            state = "leased";
            running = true;
          }
          if (path.endsWith(`/${lost}`) && lose) {
            lose = false;
            throw new Error("Response lost");
          }
          return Response.json({});
        }
        if (path.endsWith("/operator"))
          return Response.json({
            procedure: { state, revision: 12, cancellation: null },
            dispatch: { worker_running: running, management: "unmanaged" },
            current_child: null,
            current_step: null,
            child_runs: [
              { step_key: "measure", run: { control: { admission: { run_id: "run-1" } } } },
            ],
            steps: {
              items: [
                {
                  step_key: "measure",
                  attempt: 1,
                  revision: 3,
                  operation: "run",
                  state: "attention_required",
                  attention_reason: "confirm current wiring before continuing run run-1",
                },
              ],
              next_cursor: null,
            },
          });
        return Response.json({ control: { state: "queued" } });
      }),
    );
    await expect(reconfirmManualLine("run-1", "b".repeat(64), confirmation)).rejects.toThrow(
      "did not respond",
    );
    await expect(reconfirmManualLine("run-1", "b".repeat(64), confirmation)).resolves.toBe(
      "recorded",
    );
    expect(writes.filter((write) => write.path.endsWith("/retry"))).toHaveLength(1);
    expect(writes.filter((write) => write.path.endsWith("/dispatch"))).toHaveLength(1);
    const confirmations = writes.filter((write) => write.path.endsWith("/attention"));
    expect(confirmations).toHaveLength(2);
    expect(confirmations[0]).toEqual(confirmations[1]);
  },
);

it("never dispatches a changed or unknown child after reconfirmation", async () => {
  const writes: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (request.method === "POST") {
        writes.push(path);
        return Response.json({});
      }
      if (path.endsWith("/operator"))
        return Response.json({
          procedure: { state: "attention_required", revision: 12 },
          dispatch: { worker_running: false },
          child_runs: [
            { step_key: "measure", run: { control: { admission: { run_id: "another-run" } } } },
          ],
          steps: {
            items: [
              {
                state: "attention_required",
                operation: "run",
                attention_reason: "unknown effect",
              },
            ],
            next_cursor: null,
          },
        });
      return Response.json({ control: { state: "queued" } });
    }),
  );
  await expect(reconfirmManualLine("run-1", "b".repeat(64), confirmation)).rejects.toThrow(
    "任务已变化",
  );
  expect(writes).toEqual(["/api/v1/runs/run-1/attention"]);
});

it("uses the matching page revisions and newest retained attempt", async () => {
  const retries: unknown[] = [];
  const pages: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (request.method === "POST") {
        if (url.pathname.endsWith("/retry")) retries.push(await request.json());
        return Response.json({});
      }
      if (!url.pathname.endsWith("/operator"))
        return Response.json({ control: { state: "queued" } });
      pages.push(url.search);
      const matched = url.searchParams.get("cursor") === "50";
      const step = {
        step_key: "measure",
        operation: "run",
        state: "attention_required",
        attention_reason: "confirm current wiring before continuing run run-1",
      };
      return Response.json({
        procedure: { state: "attention_required", revision: matched ? 15 : 14 },
        dispatch: { worker_running: false },
        current_child: null,
        current_step: null,
        child_runs: matched
          ? [{ step_key: "measure", run: { control: { admission: { run_id: "run-1" } } } }]
          : [],
        steps: {
          items: matched
            ? [
                { ...step, attempt: 2, revision: 7 },
                { ...step, attempt: 1, revision: 3 },
              ]
            : [],
          next_cursor: matched ? null : 50,
        },
      });
    }),
  );
  await expect(reconfirmManualLine("run-1", "b".repeat(64), confirmation)).resolves.toBe(
    "recorded",
  );
  expect(pages).toEqual(["", "?cursor=50"]);
  expect(retries).toEqual([
    {
      procedure_run_id: "parent",
      step_key: "measure",
      attempt: 2,
      expected_run_revision: 15,
      expected_step_revision: 7,
    },
  ]);
});

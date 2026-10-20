import { afterEach, expect, it, vi } from "vitest";
import { submitProcedureInput } from "./decision-api";

const hash = `sha256:${"1".repeat(64)}`;
afterEach(() => vi.unstubAllGlobals());

it.each(["course-one", null])(
  "uses retained owner %s for the answer and exact retry",
  async (workspace) => {
    const headers: (string | null)[] = [];
    const command = {
      procedure_run_id: "course-procedure",
      expected_run_revision: 3,
      step_key: "decision",
      attempt: 1,
      expected_step_revision: 2,
      request_hash: hash,
      actor: "Alice",
      note: "",
      actor_kind: "human" as const,
      value: { choice: true },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        if (request.method === "GET") {
          return Response.json({
            workspace_id: workspace,
            procedure: { source: { workspace_id: "foreign-source" } },
          });
        }
        headers.push(request.headers.get("X-Scopecat-Workspace"));
        expect(await request.json()).toEqual(command);
        return Response.json({ continuation: "queued" });
      }),
    );
    await submitProcedureInput(command);
    await submitProcedureInput(command);
    expect(headers).toEqual([workspace, workspace]);
  },
);

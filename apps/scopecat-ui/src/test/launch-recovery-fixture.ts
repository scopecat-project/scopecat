import { vi } from "vitest";
import type { components } from "../api-schema";

/** In-memory HTTP fixture for consumers outside the recovery persistence tests. */
export function installLaunchRecoveryRoutes() {
  const location = new URL(window.location.href);
  if (!location.searchParams.has("workspace")) {
    location.searchParams.set("workspace", "legacy");
    window.history.replaceState(null, "", location);
  }
  const fallback = globalThis.fetch;
  let sequence = 0;
  const drafts = new Map<string, components["schemas"]["LaunchDraftRecord"]>();
  const attempts: unknown[] = [];
  vi.stubGlobal("fetch", async (request: Request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/launch-drafts/read")) {
      const target = await request.json();
      return Response.json({ head: drafts.get(JSON.stringify(target)) ?? null });
    }
    if (path.endsWith("/launch-drafts/save")) {
      const body = await request.json();
      const saved = {
        ...body,
        revision: ++sequence,
        state: "saved",
        created_at: new Date().toISOString(),
      };
      drafts.set(JSON.stringify(body.target), saved);
      return Response.json({ head: saved, saved });
    }
    if (path.endsWith("/launch-drafts"))
      return Response.json({ items: [...drafts.values()], next_cursor: null });
    if (path.endsWith("/launch-attempts") && request.method === "GET")
      return Response.json({ items: attempts, next_cursor: null });
    if (path.endsWith("/launch-attempts") && request.method === "POST") {
      const retained = {
        ...(await request.json()),
        sequence: ++sequence,
        created_at: new Date().toISOString(),
      };
      attempts.push(retained);
      return Response.json(retained);
    }
    return fallback(request);
  });
}
export const procedureDefinition = {
  id: "maintained.launch_prepared",
  version: "1",
  fingerprint: `sha256:${"1".repeat(64)}`,
};

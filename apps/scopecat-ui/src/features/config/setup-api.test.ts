import { afterEach, expect, it, vi } from "vitest";
import type { ConfigProfileSnapshot } from "../../api-contract";
import { scenarioFixture } from "../../test/scenario-fixture";
import { importSetupRecipe } from "./setup-api";

afterEach(() => vi.unstubAllGlobals());
it.each([scenarioFixture, null])(
  "preserves the declared scenario when importing a setup recipe",
  async (scenario) => {
    const config: ConfigProfileSnapshot = {
      id: "parameters",
      system: {
        id: "system",
        domain_target: null,
        topology: { entities: [] },
        instrument_registry: { instruments: [] },
        parameter_catalog: { id: "params", definitions: [] },
        scenario,
      },
      parameter_snapshot: { id: "params", values: [] },
    };
    const bodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        bodies.push(await request.json());
        return Response.json({});
      }),
    );
    await importSetupRecipe(config, "setup-revision", "operator");
    expect(bodies).toEqual([
      expect.objectContaining({ setup: expect.objectContaining({ scenario }) }),
    ]);
  },
);

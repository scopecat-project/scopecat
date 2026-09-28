import { afterEach, describe, expect, it, vi } from "vitest";
import { requestPath } from "../../test/http";
import { getOlderRunParameterProposals, getRunParameterProposals } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("parameter proposal reads", () => {
  it("normalizes proposal deltas and durable approval", async () => {
    const fetchMock = vi.fn((_input: string | URL | Request) =>
      Promise.resolve(
        jsonResponse({
          run_id: "run/a",
          items: [
            {
              proposal: {
                id: "drive-frequency",
                source_run_id: "run/a",
                analysis_record_id: "analysis-fit-r1",
                base_config_id: "baseline",
                base_config_content_hash: "sha256:base",
                reason: "Peak moved",
                evidence_output_ids: ["selected-fit", "fit-quality"],
                confidence: 0.92,
                proposed_at: "2026-07-23T10:00:00Z",
                deltas: [
                  {
                    parameter_id: "q0.drive.frequency",
                    before: scalarValue(5.0),
                    after: scalarValue(5.1),
                  },
                ],
              },
              approval: {
                run_id: "run/a",
                proposal_id: "drive-frequency",
                actor: "nightly-calibration",
                note: "Peak is clean",
                approved_at: "2026-07-23T10:02:00Z",
              },
            },
          ],
          next_cursor: 17,
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getRunParameterProposals("run/a");

    expect(requestPath(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/runs/run%2Fa/parameter-proposals?limit=100",
    );
    expect(result).toEqual({
      runId: "run/a",
      items: [
        {
          id: "drive-frequency",
          sourceRunId: "run/a",
          analysisRecordId: "analysis-fit-r1",
          baseConfigId: "baseline",
          baseContentHash: "sha256:base",
          reason: "Peak moved",
          evidenceOutputIds: ["selected-fit", "fit-quality"],
          confidence: 0.92,
          proposedAt: "2026-07-23T10:00:00Z",
          deltas: [
            {
              parameterId: "q0.drive.frequency",
              before: 5,
              after: 5.1,
            },
          ],
          approval: {
            actor: "nightly-calibration",
            note: "Peak is clean",
            approvedAt: "2026-07-23T10:02:00Z",
          },
        },
      ],
      nextCursor: 17,
    });
  });

  it("requests an older proposal page by cursor", async () => {
    const fetchMock = vi.fn((_input: string | URL | Request) =>
      Promise.resolve(jsonResponse({ run_id: "run-a", items: [], next_cursor: null })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getOlderRunParameterProposals("run-a", 17);

    expect(requestPath(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/runs/run-a/parameter-proposals?limit=100&before=17",
    );
  });
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function scalarValue(value: number) {
  return {
    id: "q0.drive.frequency",
    shape: "scalar",
    value,
  };
}

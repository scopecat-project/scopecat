// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { components } from "../../api-schema";
import { CalibrationEvidence } from "./CalibrationEvidence";

const stage: components["schemas"]["CalibrationTaskStage"] = {
  id: "readout",
  depends_on: [],
  check: {
    codec: "scopecat.calibration-check.v1",
    result_output: "check",
    measurement_step: "measure",
    analysis_step: "analyze",
    scope: {
      capability: "readout",
      targets: ["q0", "q1"],
      conditions: "joint",
      policy_version: "2",
    },
    context: {
      parameters: { revision_id: "frozen", content_hash: "sha256:parameters" },
      subject: { kind: "unbound" },
      scenario: null,
      setup_content_hash: "sha256:setup",
    },
  },
};
function report(
  status: components["schemas"]["CheckStatus"],
): components["schemas"]["CalibrationReport"] {
  return {
    context: stage.check.context,
    observed_at: "2026-09-23T08:00:00Z",
    items: [
      {
        requirement: { id: stage.id, scope: stage.check.scope, max_age: "PT7200S", depends_on: [] },
        availability: { status, blocked_by: [] },
        scanned: 2,
        unresolved_procedures: [],
        incomplete_reasons: [],
        selection: {
          status,
          reason: "latest_matching",
          evidence: null,
          assessment: {
            run_id: "r1",
            status,
            reasons: [status === "usable" ? "within_spec" : "out_of_spec"],
          },
        },
      },
    ],
  };
}
function mount(onProcedure = vi.fn()) {
  render(<CalibrationEvidence stage={stage} onProcedure={onProcedure} />);
  fireEvent.click(screen.getByText("Inspect applicable evidence for readout"));
  return onProcedure;
}
function inspect() {
  fireEvent.change(screen.getByLabelText("Maximum evidence age (hours)"), {
    target: { value: "2" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Check evidence for readout" }));
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("requires an explicit age and submits exactly the stage's frozen scope and context", async () => {
  const requests: unknown[] = [];
  const fetcher = vi.fn(async (request: Request) => {
    requests.push(await request.json());
    expect(new URL(request.url).pathname).toBe("/api/v1/calibration-checks/report");
    return Response.json(report("out_of_spec"));
  });
  vi.stubGlobal("fetch", fetcher);
  mount();
  expect(fetcher).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Check evidence for readout" })).toBeDisabled();
  inspect();
  await screen.findByText("Out of specification");
  expect(requests).toEqual([
    {
      context: stage.check.context,
      history_limit: 50,
      requirements: [
        { id: stage.id, scope: stage.check.scope, max_age: "PT7200S", depends_on: [] },
      ],
    },
  ]);
  expect(screen.getByText(/does not assess the current branch head/)).toBeInTheDocument();
  expect(screen.getByText(/The selected check did not meet/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Maximum evidence age (hours)"), {
    target: { value: "1" },
  });
  expect(
    screen.queryByRole("region", { name: "Evidence report for readout" }),
  ).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("explains an incomplete scan and opens unresolved executions", async () => {
  const incomplete = report("unknown");
  incomplete.items[0]!.selection = {
    status: "unknown",
    reason: "incomplete_history",
    evidence: null,
    assessment: null,
  };
  incomplete.items[0]!.unresolved_procedures = ["p1"];
  incomplete.items[0]!.incomplete_reasons = ["scan_limit", "unresolved_checks"];
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(incomplete)),
  );
  const open = mount();
  inspect();
  await screen.findByText("Unknown");
  expect(screen.getByText(/More checks exist than the history limit/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Inspect unresolved execution p1" }));
  expect(open).toHaveBeenCalledWith("p1");
});

it("removes a previously usable result when refresh fails", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(report("usable")))
    .mockResolvedValueOnce(Response.json({ detail: "evidence unavailable" }, { status: 409 }));
  vi.stubGlobal("fetch", fetcher);
  mount();
  inspect();
  await screen.findByText("Usable evidence");
  fireEvent.click(screen.getByRole("button", { name: "Check evidence for readout" }));
  await screen.findByText("evidence unavailable");
  expect(screen.queryByText("Usable evidence")).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(2);
});

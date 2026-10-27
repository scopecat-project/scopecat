// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { components } from "../../api-schema";
import { CalibrationEvidence } from "./CalibrationEvidence";

const stage: components["schemas"]["CalibrationTaskStage"] = {
  id: "readout",
  depends_on: [],
  check: {
    setup: { revision_id: "bench", content_hash: "sha256:setup" },
    codec: "scopecat.calibration-check.v1",
    result_output: "check",
    measurement_step: "measure",
    analysis_step: "analyze",
    dependencies: { qualification: "review-v1", physical: null },
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
        assessments: [],
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
  fireEvent.click(screen.getByText("检查适用证据： readout"));
  return onProcedure;
}
function inspect() {
  fireEvent.change(screen.getByLabelText("证据最大有效时长（小时）"), {
    target: { value: "2" },
  });
  fireEvent.click(screen.getByRole("button", { name: "检查证据： readout" }));
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("selects a saved policy explicitly and shows all capability verdicts without dispatch", async () => {
  const own = report("usable").items[0]!;
  const blocked = {
    ...own,
    requirement: { ...own.requirement, id: "gate", depends_on: [stage.id] },
    availability: { status: "blocked", blocked_by: [stage.id] },
  };
  const result = {
    ...report("unknown"),
    profile_id: "daily-v1",
    items: [report("unknown").items[0]!, blocked],
  };
  const bodies: unknown[] = [];
  let fail = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      expect(request.method).toBe(url.pathname === "/api/v1/calibration-profiles" ? "GET" : "POST");
      if (url.pathname === "/api/v1/calibration-profiles") {
        return Response.json({
          items: [
            {
              created_at: "2026-09-23T00:00:00Z",
              profile: {
                id: "daily-v1",
                description: "Lab daily policy",
                requirements: [own.requirement, blocked.requirement],
              },
            },
          ],
          next_cursor: null,
        });
      }
      expect(url.pathname).toBe("/api/v1/calibration-profiles/daily-v1/report");
      bodies.push(await request.json());
      if (fail) throw new Error("offline");
      return Response.json(result);
    }),
  );
  mount();
  fireEvent.click(screen.getByText("检查已保存的能力 profile"));
  fireEvent.click(screen.getByText("加载已保存的 profile"));
  const select = await screen.findByLabelText("能力 profile");
  fireEvent.change(select, { target: { value: "daily-v1" } });
  expect(screen.getByText("Lab daily policy")).toBeInTheDocument();
  expect(bodies).toEqual([]);
  fireEvent.click(screen.getByText("检查已保存的 profile"));
  await screen.findByRole("region", { name: "已保存的能力报告" });
  expect(screen.getByText("自身检查： 证据可用 · 可用性： 受前置条件阻塞")).toBeInTheDocument();
  expect(screen.getByText("阻塞项： readout")).toBeInTheDocument();
  expect(bodies).toEqual([{ context: stage.check.context, history_limit: 50 }]);
  fireEvent.click(screen.getByLabelText("包含 gate"));
  expect(screen.queryByRole("region", { name: "已保存的能力报告" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("检查已保存的 profile"));
  await screen.findByRole("region", { name: "已保存的能力报告" });
  expect(bodies[1]).toEqual({
    context: stage.check.context,
    history_limit: 50,
    requirement_ids: ["gate"],
  });
  fail = true;
  fireEvent.click(screen.getByText("检查已保存的 profile"));
  await screen.findByRole("alert");
  expect(screen.queryByRole("region", { name: "已保存的能力报告" })).not.toBeInTheDocument();
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
  expect(screen.getByRole("button", { name: "检查证据： readout" })).toBeDisabled();
  inspect();
  await screen.findByText("不符合要求");
  expect(requests).toEqual([
    {
      context: stage.check.context,
      history_limit: 50,
      requirements: [
        {
          id: stage.id,
          scope: stage.check.scope,
          max_age: "PT7200S",
          depends_on: [],
          dependencies: stage.check.dependencies,
        },
      ],
    },
  ]);
  expect(screen.getByText(/不评估当前分支头/)).toBeInTheDocument();
  expect(screen.getByText(/所选检查未满足/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("证据最大有效时长（小时）"), {
    target: { value: "1" },
  });
  expect(screen.queryByRole("region", { name: "证据报告： readout" })).not.toBeInTheDocument();
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
  await screen.findByText("未知");
  expect(screen.getByText(/检查数量超过历史上限/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看未解决的执行： p1" }));
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
  await screen.findByText("证据可用");
  fireEvent.click(screen.getByRole("button", { name: "检查证据： readout" }));
  await screen.findByText("evidence unavailable");
  expect(screen.queryByText("证据可用")).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(2);
});

for (const inspector of ["evidence", "profile"] as const) {
  for (const late of [false, true]) {
    it(`${inspector} binds ${late ? "late" : "completed"} reports to the exact context on a mounted stage`, async () => {
      let finishA!: () => void;
      const requests: unknown[] = [];
      vi.stubGlobal("fetch", async (request: Request) => {
        if (request.method === "GET")
          return Response.json({
            items: [
              {
                created_at: "2026-09-23T00:00:00Z",
                profile: {
                  id: "daily-v1",
                  description: "Policy",
                  requirements: [report("usable").items[0]!.requirement],
                },
              },
            ],
            next_cursor: null,
          });
        const body = await request.json();
        requests.push(body);
        if (requests.length === 1) {
          if (late)
            await new Promise<void>((resolve) => {
              finishA = resolve;
            });
          return Response.json(report("usable"));
        }
        return Response.json({ detail: "B unavailable" }, { status: 409 });
      });
      const onProcedure = vi.fn();
      const view = render(<CalibrationEvidence stage={stage} onProcedure={onProcedure} />);
      async function check() {
        const detail = screen.getByText("检查适用证据： readout").closest("details")!;
        if (!detail.open) fireEvent.click(screen.getByText("检查适用证据： readout"));
        if (inspector === "evidence") inspect();
        else {
          const load = screen.queryByText("加载已保存的 profile");
          if (load) fireEvent.click(load);
          fireEvent.change(await screen.findByLabelText("能力 profile"), {
            target: { value: "daily-v1" },
          });
          fireEvent.click(screen.getByText("检查已保存的 profile"));
        }
      }
      const name = inspector === "evidence" ? "证据报告： readout" : "已保存的能力报告";
      await check();
      if (late)
        await act(async () => {
          await Promise.resolve();
        });
      else await screen.findByRole("region", { name, hidden: true });
      const next = structuredClone(stage);
      next.check.context.parameters = { revision_id: "candidate-B", content_hash: "sha256:B" };
      view.rerender(<CalibrationEvidence stage={next} onProcedure={onProcedure} />);
      if (late)
        await act(async () => {
          finishA();
        });
      expect(screen.queryByRole("region", { name, hidden: true })).not.toBeInTheDocument();
      expect(requests).toHaveLength(1);
      await check();
      await screen.findByText("B unavailable");
      expect(screen.queryByRole("region", { name, hidden: true })).not.toBeInTheDocument();
      expect(requests).toHaveLength(2);
      expect(requests[1]).toMatchObject({ context: next.check.context });
    });
  }
}

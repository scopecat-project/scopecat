// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api-client";
import type { ReviewSession } from "../../api-contract";
import { ReviewWorkspace } from "./ReviewWorkspace";
import { compileReviewPoint, getReview, getReviews } from "./review-api";

type ProgramLayer = NonNullable<
  NonNullable<ReviewSession["latest_result"]>["inspections"][number]["content"]["program"]
>["layers"][number];

vi.mock("./review-api", () => ({
  compileReviewPoint: vi.fn(),
  getReview: vi.fn(),
  getReviews: vi.fn(),
}));

vi.mock("../../ui/EChart", () => ({
  EChart: ({ ariaLabel }: { ariaLabel: string }) => <div role="img" aria-label={ariaLabel} />,
}));

beforeEach(() => {
  vi.clearAllMocks();
  const value = reviewSession();
  vi.mocked(getReviews).mockResolvedValue({ items: [value] });
  vi.mocked(getReview).mockResolvedValue(value);
  vi.mocked(compileReviewPoint).mockResolvedValue();
  window.history.replaceState(null, "", "/#reviews/review-1");
});

afterEach(() => {
  cleanup();
});

describe("ReviewWorkspace", () => {
  it.each(["list", "detail", "daemon"] as const)(
    "retains unsubmitted coordinates and mode after a temporary %s failure",
    async (source) => {
      const { client, setDaemonUnavailable } = renderWorkspace();
      await screen.findByText("编译选点");
      fireEvent.change(screen.getByRole("spinbutton", { name: "amplification" }), {
        target: { value: "3" },
      });
      fireEvent.click(screen.getByRole("button", { name: "自由坐标" }));
      if (source === "daemon") {
        setDaemonUnavailable(true);
      } else {
        const fetch = source === "list" ? vi.mocked(getReviews) : vi.mocked(getReview);
        fetch.mockRejectedValue(new Error("temporary failure"));
        await act(() =>
          client.refetchQueries({
            queryKey: source === "list" ? ["reviews"] : ["review", "review-1"],
          }),
        );
      }
      await waitFor(() => expect(screen.getByRole("button", { name: "编译波形" })).toBeDisabled());
      fireEvent.click(screen.getByRole("button", { name: "编译波形" }));
      fireEvent.submit(screen.getByRole("searchbox", { name: "搜索程序节点" }).closest("form")!);
      expect(compileReviewPoint).not.toHaveBeenCalled();
      vi.mocked(getReviews).mockResolvedValue({ items: [reviewSession()] });
      vi.mocked(getReview).mockResolvedValue(reviewSession());
      if (source === "daemon") setDaemonUnavailable(false);
      else
        await act(() =>
          client.refetchQueries({
            queryKey: source === "list" ? ["reviews"] : ["review", "review-1"],
          }),
        );
      await waitFor(() => expect(screen.getByRole("button", { name: "编译波形" })).toBeEnabled());
      expect(screen.getByRole("spinbutton", { name: "amplification" })).toHaveValue(3);
      expect(screen.getByRole("button", { name: "自由坐标" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    },
  );

  it("does not carry a failed session's draft into another session", async () => {
    const other = { ...reviewSession(), session_id: "review-2", title: "Other review" };
    vi.mocked(getReviews).mockResolvedValue({ items: [reviewSession(), other] });
    vi.mocked(getReview).mockImplementation(async (id) =>
      id === "review-2" ? other : reviewSession(),
    );
    const { client } = renderWorkspace();
    await screen.findByText("编译选点");
    fireEvent.change(screen.getByRole("spinbutton", { name: "amplification" }), {
      target: { value: "3" },
    });
    fireEvent.click(screen.getByRole("button", { name: "自由坐标" }));
    vi.mocked(getReview).mockImplementation(async (id) => {
      if (id === "review-1") throw new Error("temporary failure");
      return other;
    });
    await act(() => client.refetchQueries({ queryKey: ["review", "review-1"] }));
    await waitFor(() => expect(screen.getByRole("button", { name: "编译波形" })).toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: /Other review/ }));
    await screen.findByText("会话：review-2");
    expect(screen.getByRole("spinbutton", { name: "amplification" })).toHaveValue(1);
    expect(screen.getByRole("button", { name: "精确匹配" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(window.location.hash).toBe("#reviews/review-2");
  });

  it("keeps inactive recovery read-only and treats a confirmed 404 as missing", async () => {
    const { client } = renderWorkspace();
    await screen.findByText("编译选点");
    fireEvent.change(screen.getByRole("spinbutton", { name: "amplification" }), {
      target: { value: "3" },
    });
    vi.mocked(getReview).mockRejectedValue(new Error("temporary failure"));
    await act(() => client.refetchQueries({ queryKey: ["review", "review-1"] }));
    vi.mocked(getReview).mockResolvedValue({ ...reviewSession(), active: false });
    await act(() => client.refetchQueries({ queryKey: ["review", "review-1"] }));
    await screen.findByText("编译器已断开");
    expect(screen.getByRole("spinbutton", { name: "amplification" })).toHaveValue(3);
    expect(screen.getByRole("button", { name: "编译波形" })).toBeDisabled();
    vi.mocked(getReview).mockRejectedValue(new ApiError("missing", 404));
    await act(() => client.refetchQueries({ queryKey: ["review", "review-1"] }));
    expect(await screen.findByText("指定的检查会话不存在")).toBeVisible();
    expect(screen.queryByRole("spinbutton", { name: "amplification" })).not.toBeInTheDocument();
    expect(window.location.hash).toBe("#reviews/review-1");
    expect(compileReviewPoint).not.toHaveBeenCalled();
  });

  it("does not infer a missing exact session from a failed list refresh", async () => {
    const { client } = renderWorkspace();
    await screen.findByText("编译选点");
    vi.mocked(getReviews).mockRejectedValue(new Error("temporary failure"));
    await act(() => client.refetchQueries({ queryKey: ["reviews"] }));
    act(() => {
      window.history.replaceState(null, "", "/#reviews/unknown");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(await screen.findByText("暂时无法确认检查会话")).toBeVisible();
    expect(screen.queryByText("指定的检查会话不存在")).not.toBeInTheDocument();
    vi.mocked(getReviews).mockResolvedValue({ items: [reviewSession()] });
    await act(() => client.refetchQueries({ queryKey: ["reviews"] }));
    expect(await screen.findByText("指定的检查会话不存在")).toBeVisible();
    expect(window.location.hash).toBe("#reviews/unknown");
  });

  it("shows Chinese empty and loading states", async () => {
    window.history.replaceState(null, "", "/#reviews");
    vi.mocked(getReviews).mockResolvedValue({ items: [] });
    renderWorkspace();
    expect(screen.getByText("正在加载检查会话")).toBeVisible();
    expect(await screen.findByText("暂无实验检查会话")).toBeVisible();
  });

  it("keeps exact identity on a list error", async () => {
    vi.mocked(getReviews).mockRejectedValue(new Error("diagnostic"));
    renderWorkspace();
    expect(await screen.findByText("无法加载检查会话")).toBeVisible();
    expect(window.location.hash).toBe("#reviews/review-1");
  });

  it("shows submission failure without replacing the completed result", async () => {
    vi.mocked(compileReviewPoint).mockRejectedValue(new Error("diagnostic"));
    renderWorkspace();
    await screen.findByText("编译选点");
    fireEvent.click(screen.getByRole("button", { name: "编译波形" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("提交失败：diagnostic");
    expect(screen.getByText("结果请求：initial")).toBeVisible();
  });

  it("preserves a missing exact identity and allows explicit reselection", async () => {
    window.history.replaceState(null, "", "/#reviews/missing");
    renderWorkspace();
    expect(await screen.findByText("指定的检查会话不存在")).toBeVisible();
    expect(window.location.hash).toBe("#reviews/missing");
    expect(getReview).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /DRAG beta review/ }));
    expect(await screen.findByText("编译选点")).toBeVisible();
    expect(window.location.hash).toBe("#reviews/review-1");
  });

  it("defaults only an entry without an ID and keeps it fixed when the list changes", async () => {
    window.history.replaceState(null, "", "/#reviews");
    const { client } = renderWorkspace();
    await screen.findByText("编译选点");
    expect(window.location.hash).toBe("#reviews/review-1");
    const other = { ...reviewSession(), session_id: "other", title: "Other" };
    await act(() => client.setQueryData(["reviews"], { items: [other] }));
    expect(await screen.findByText("指定的检查会话不存在")).toBeVisible();
    expect(window.location.hash).toBe("#reviews/review-1");
  });

  it("responds to same-workspace history changes and malformed exact IDs", async () => {
    renderWorkspace();
    await screen.findByText("编译选点");
    act(() => {
      window.history.replaceState(null, "", "/#reviews/%broken");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(await screen.findByText("指定的检查会话不存在")).toBeVisible();
    expect(window.location.hash).toBe("#reviews/%broken");
    act(() => {
      window.history.replaceState(null, "", "/#reviews/review-1");
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(await screen.findByText("编译选点")).toBeVisible();
  });

  it("labels shared pending work and the previous result without replacing typed coordinates", async () => {
    const { client } = renderWorkspace();
    await screen.findByText("编译选点");
    fireEvent.change(screen.getByRole("spinbutton", { name: "amplification" }), {
      target: { value: "3" },
    });
    const pending = {
      ...reviewSession(),
      pending_request_count: 1,
      latest_request: {
        session_id: "review-1",
        request_id: "other-request",
        coordinates: { amplification: 2 },
        coordinate_mode: "free" as const,
      },
    };
    await act(() => client.setQueryData(["review", "review-1"], pending));
    expect(await screen.findByText(/上一结果（新请求尚未完成）/)).toBeVisible();
    expect(screen.getByText("请求 other-request")).toBeVisible();
    expect(screen.getByText("结果请求：initial")).toBeVisible();
    expect(screen.getByText("2026-08-13T09:00:01Z")).toBeVisible();
    expect(screen.getByRole("button", { name: "编译波形" })).toBeEnabled();
    await act(() =>
      client.setQueryData(["review", "review-1"], {
        ...pending,
        pending_request_count: 0,
        latest_result: {
          ...pending.latest_result,
          request_id: "other-request",
          point: { point_index: 1, coordinates: { amplification: 2 }, source: "author" },
        },
      }),
    );
    expect(await screen.findByText("最新请求：已完成")).toBeVisible();
    expect(screen.getByRole("spinbutton", { name: "amplification" })).toHaveValue(3);
  });

  it("shows failed result identity, disconnected state and input validation in Chinese", async () => {
    const { client } = renderWorkspace();
    await screen.findByText("编译选点");
    fireEvent.change(screen.getByRole("spinbutton", { name: "amplification" }), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "编译波形" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("amplification 需要填写值");
    expect(compileReviewPoint).not.toHaveBeenCalled();
    await act(() =>
      client.setQueryData(["review", "review-1"], {
        ...reviewSession(),
        active: false,
        latest_result: {
          request_id: "failed",
          completed_at: "2026-08-13T09:00:02Z",
          inspections: [],
          error: "compiler diagnostic",
        },
      }),
    );
    expect(await screen.findByText("结果请求：failed")).toBeVisible();
    expect(screen.getByText("compiler diagnostic")).toBeVisible();
    expect(screen.getByRole("button", { name: "编译波形" })).toBeDisabled();
  });

  it("shows typed waveform inspection and physical realization identity", async () => {
    renderWorkspace();

    expect(await screen.findByText("编译选点")).toBeVisible();
    expect(screen.getByText("计划点 #1")).toBeVisible();
    expect(screen.getByRole("img", { name: "编译后的物理波形" })).toBeVisible();
    expect(screen.getByText("awg-1/outputs/i")).toBeVisible();
    expect(screen.getByText("sha256:realization")).toBeVisible();
    expect(screen.getByText("量子程序")).toBeVisible();
    expect(screen.getByLabelText("程序层级总览")).toBeVisible();
    expect(screen.getByText("设计意图")).toBeVisible();
    expect(screen.getByText("时序事件")).toBeVisible();
    expect(screen.getByRole("tab", { name: /已调度脉冲事件/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("play drive(q0)")).toBeVisible();
    fireEvent.click(screen.getByText("play drive(q0)"));
    expect(screen.getByText("实体 q0")).toBeVisible();
  });

  it("navigates lowering lineage across already loaded abstraction layers", async () => {
    renderWorkspace();
    await screen.findByText("量子程序");

    fireEvent.click(screen.getByText("play drive(q0)"));
    fireEvent.click(
      screen.getByRole("button", {
        name: "打开转换关系 绑定逻辑程序 x90(q0)",
      }),
    );

    expect(screen.getByRole("tab", { name: /绑定逻辑程序/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("button", { name: /x90\(q0\)/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "← 返回 play drive(q0)" })).toBeVisible();
  });

  it("queries an exact lineage node when its layer page is not loaded", async () => {
    const value = reviewSession();
    const logical = value.latest_result?.inspections[0]?.content.program?.layers.find(
      (layer) => layer.id === "logical",
    );
    expect(logical).toBeDefined();
    if (logical) {
      logical.nodes = [];
      logical.nodes_truncated = true;
      logical.page.returned_node_count = 0;
    }
    vi.mocked(getReviews).mockResolvedValue({ items: [value] });
    vi.mocked(getReview).mockResolvedValue(value);

    renderWorkspace();
    await screen.findByText("量子程序");
    fireEvent.click(screen.getByText("play drive(q0)"));
    fireEvent.click(
      screen.getByRole("button", {
        name: "打开转换关系 绑定逻辑程序 logical:operation:body/gate",
      }),
    );

    await waitFor(() =>
      expect(compileReviewPoint).toHaveBeenCalledWith("review-1", {
        point_index: 0,
        coordinate_mode: "exact",
        inspection_query: {
          layer_id: "logical",
          snapshot_id: "sha256:artifact",
          node_id: "logical:operation:body/gate",
          offset: 0,
          limit: 128,
        },
      }),
    );
  });

  it("shows selected placement candidates and structured rejection reasons", async () => {
    const value = reviewSession();
    const program = value.latest_result?.inspections[0]?.content.program;
    expect(program).toBeDefined();
    program?.layers.splice(-1, 0, placementLayer());
    vi.mocked(getReviews).mockResolvedValue({ items: [value] });
    vi.mocked(getReview).mockResolvedValue(value);

    renderWorkspace();
    await screen.findByText("量子程序");
    fireEvent.click(screen.getByRole("tab", { name: /物理映射/ }));

    expect(screen.getByText("已选用", { selector: "span" })).toBeVisible();
    expect(screen.getByText("已拒绝", { selector: "span" })).toBeVisible();
    fireEvent.click(screen.getByText("rejected drive(q0) → awg-2:q0"));
    expect(screen.getByText("路由已拒绝")).toBeVisible();
    expect(screen.getByText("requested q0 but route is configured for q1")).toBeVisible();

    fireEvent.click(screen.getByText("selected drive(q0) → awg-1:q0"));
    expect(screen.getByText("路由已选用")).toBeVisible();
  });

  it("queries one bounded program layer without changing the selected point", async () => {
    renderWorkspace();
    await screen.findByText("量子程序");

    fireEvent.change(screen.getByRole("searchbox", { name: "搜索程序节点" }), {
      target: { value: "drive" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "程序实体" }), {
      target: { value: "q0" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "程序结果" }), {
      target: { value: "readout" },
    });
    fireEvent.click(screen.getByRole("button", { name: "查询程序层" }));

    await waitFor(() =>
      expect(compileReviewPoint).toHaveBeenCalledWith("review-1", {
        point_index: 0,
        coordinate_mode: "exact",
        inspection_query: {
          layer_id: "scheduled",
          snapshot_id: "sha256:artifact",
          offset: 0,
          limit: 128,
          text: "drive",
          entity_id: "q0",
          result_id: "readout",
        },
      }),
    );
  });

  it("requests the next server page for a large program layer", async () => {
    renderWorkspace();
    await screen.findByText("量子程序");

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    await waitFor(() =>
      expect(compileReviewPoint).toHaveBeenCalledWith("review-1", {
        point_index: 0,
        coordinate_mode: "exact",
        inspection_query: {
          layer_id: "scheduled",
          snapshot_id: "sha256:artifact",
          cursor: "128.cursor",
          offset: 0,
          limit: 128,
        },
      }),
    );
  });

  it("submits off-grid coordinates without snapping to the planned point", async () => {
    renderWorkspace();
    await screen.findByText("编译选点");

    fireEvent.click(screen.getByRole("button", { name: "自由坐标" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: /beta/i }), {
      target: { value: "0.137" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: "amplification" }), {
      target: { value: "2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "编译波形" }));

    await waitFor(() =>
      expect(compileReviewPoint).toHaveBeenCalledWith("review-1", {
        coordinate_mode: "free",
        coordinates: {
          amplification: 2,
          beta: { unit: "ns", value: 0.137 },
        },
      }),
    );
  });

  it.each(["exact", "snap"] as const)(
    "submits %s matching as an explicit coordinate mode",
    async (mode) => {
      renderWorkspace();
      await screen.findByText("编译选点");

      fireEvent.click(
        screen.getByRole("button", { name: { exact: "精确匹配", snap: "就近选点" }[mode] }),
      );
      fireEvent.click(screen.getByRole("button", { name: "编译波形" }));

      await waitFor(() =>
        expect(compileReviewPoint).toHaveBeenCalledWith(
          "review-1",
          expect.objectContaining({ coordinate_mode: mode }),
        ),
      );
    },
  );
});

function renderWorkspace() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  const content = (unavailable: boolean) => (
    <QueryClientProvider client={client}>
      <ReviewWorkspace daemonUnavailable={unavailable} />
    </QueryClientProvider>
  );
  const view = render(content(false));
  return {
    client,
    ...view,
    setDaemonUnavailable: (unavailable: boolean) => view.rerender(content(unavailable)),
  };
}

function placementLayer(): ProgramLayer {
  return {
    id: "physical",
    label: "Physical placement",
    kind: "physical",
    node_count: 3,
    nodes_truncated: false,
    root_ids: ["physical:placement"],
    facts: [],
    page: {
      offset: 0,
      limit: 128,
      matching_node_count: 3,
      returned_node_count: 3,
      snapshot_id: "sha256:artifact",
    },
    nodes: [
      {
        id: "physical:placement",
        kind: "device",
        label: "device list-mode",
        child_count: 2,
        entity_ids: ["q0"],
        entity_count: 1,
        entity_ids_truncated: false,
        resource_ids: ["awg-1:q0", "awg-2:q0"],
        resource_count: 2,
        resource_ids_truncated: false,
        result_ids: [],
        result_count: 0,
        result_ids_truncated: false,
        facts: [],
        warnings: [],
      },
      {
        id: "physical:candidate:selected",
        kind: "placement_candidate_selected",
        label: "selected drive(q0) → awg-1:q0",
        parent_id: "physical:placement",
        child_count: 0,
        entity_ids: ["q0"],
        entity_count: 1,
        entity_ids_truncated: false,
        resource_ids: ["awg-1:q0"],
        resource_count: 1,
        resource_ids_truncated: false,
        result_ids: [],
        result_count: 0,
        result_ids_truncated: false,
        facts: [{ id: "status", value: "selected" }],
        warnings: [],
      },
      {
        id: "physical:candidate:rejected",
        kind: "placement_candidate_rejected",
        label: "rejected drive(q0) → awg-2:q0",
        parent_id: "physical:placement",
        child_count: 0,
        entity_ids: ["q0", "q1"],
        entity_count: 2,
        entity_ids_truncated: false,
        resource_ids: ["awg-2:q0"],
        resource_count: 1,
        resource_ids_truncated: false,
        result_ids: [],
        result_count: 0,
        result_ids_truncated: false,
        facts: [
          { id: "status", value: "rejected" },
          { id: "rejection_codes", value: ["entity_mismatch"] },
        ],
        warnings: ["requested q0 but route is configured for q1"],
      },
    ],
  };
}

function reviewSession(): ReviewSession {
  return {
    active: true,
    heartbeat_interval_seconds: 5,
    coordinates: [
      {
        id: "beta",
        kind: "quantity",
        unit: "ns",
        minimum: -1,
        maximum: 1,
        finite: true,
        sampled_values: [{ value: 0, unit: "ns" }],
        sampled_values_truncated: false,
      },
      {
        id: "amplification",
        kind: "int",
        minimum: 1,
        maximum: 3,
        finite: true,
        sampled_values: [1, 2, 3],
        sampled_values_truncated: false,
      },
    ],
    created_at: "2026-08-13T09:00:00Z",
    experiment_id: "drag-beta",
    experiment_kind: "experiment",
    latest_result: {
      completed_at: "2026-08-13T09:00:01Z",
      request_id: "initial",
      inspections: [
        {
          operation_id: "capture",
          point_index: 0,
          target_id: "reference-lab.list-mode",
          artifact_id: "artifact-1",
          artifact_fingerprint: "sha256:artifact",
          content: {
            schema_id: "scopecat.compiled_artifact_inspection.v2",
            kind: "reference_lab.list_mode.v1",
            work_estimates: [],
            facts: [{ id: "sample_rate_hz", value: 1_000_000_000, unit: "Hz" }],
            point_count: 1,
            points_truncated: false,
            bounds: {
              max_points: 1,
              max_waveforms_per_point: 12,
              max_samples_per_waveform: 256,
            },
            program: {
              schema_id: "scopecat.compiled_program_inspection.v3",
              dialect_id: "scopecat.quantum.program",
              program_id: "ramsey",
              snapshot_id: "sha256:artifact",
              warnings: [],
              links: [
                {
                  source_layer_id: "logical",
                  source_node_id: "logical:operation:body/gate",
                  target_layer_id: "scheduled",
                  target_node_id: "scheduled:event:drive",
                  relation: "lowers_to",
                },
              ],
              layers: [
                {
                  id: "authored",
                  label: "Authored program",
                  kind: "authored",
                  node_count: 1,
                  nodes_truncated: false,
                  root_ids: ["authored:program"],
                  facts: [],
                  page: {
                    offset: 0,
                    limit: 128,
                    matching_node_count: 1,
                    returned_node_count: 1,
                    snapshot_id: "sha256:artifact",
                  },
                  nodes: [
                    {
                      id: "authored:program",
                      kind: "program",
                      label: "program ramsey",
                      child_count: 0,
                      entity_ids: [],
                      entity_count: 0,
                      entity_ids_truncated: false,
                      resource_ids: [],
                      resource_count: 0,
                      resource_ids_truncated: false,
                      result_ids: ["iq"],
                      result_count: 1,
                      result_ids_truncated: false,
                      facts: [],
                      warnings: [],
                    },
                  ],
                },
                {
                  id: "logical",
                  label: "Bound logical program",
                  kind: "logical",
                  node_count: 1,
                  nodes_truncated: false,
                  root_ids: ["logical:operation:body/gate"],
                  facts: [],
                  page: {
                    offset: 0,
                    limit: 128,
                    matching_node_count: 1,
                    returned_node_count: 1,
                    snapshot_id: "sha256:artifact",
                  },
                  nodes: [
                    {
                      id: "logical:operation:body/gate",
                      kind: "gate",
                      label: "x90(q0)",
                      child_count: 0,
                      entity_ids: ["q0"],
                      entity_count: 1,
                      entity_ids_truncated: false,
                      resource_ids: [],
                      resource_count: 0,
                      resource_ids_truncated: false,
                      result_ids: [],
                      result_count: 0,
                      result_ids_truncated: false,
                      facts: [],
                      warnings: [],
                    },
                  ],
                },
                {
                  id: "scheduled",
                  label: "Scheduled pulse events",
                  kind: "scheduled",
                  node_count: 129,
                  nodes_truncated: true,
                  root_ids: ["scheduled:event:drive"],
                  facts: [],
                  page: {
                    offset: 0,
                    limit: 128,
                    matching_node_count: 129,
                    returned_node_count: 1,
                    next_offset: 128,
                    next_cursor: "128.cursor",
                    snapshot_id: "sha256:artifact",
                  },
                  nodes: [
                    {
                      id: "scheduled:event:drive",
                      kind: "play",
                      label: "play drive(q0)",
                      child_count: 0,
                      entity_ids: ["q0"],
                      entity_count: 1,
                      entity_ids_truncated: false,
                      resource_ids: ["awg-1/outputs/i"],
                      resource_count: 1,
                      resource_ids_truncated: false,
                      result_ids: [],
                      result_count: 0,
                      result_ids_truncated: false,
                      start_seconds: "0",
                      duration_seconds: "1e-8",
                      facts: [],
                      warnings: [],
                    },
                  ],
                },
              ],
            },
            warnings: [],
            points: [
              {
                realization_fingerprint: "sha256:realization",
                target_entry_id: "point-0",
                facts: [{ id: "sample_count", value: 4 }],
                waveform_count: 1,
                waveforms_truncated: false,
                warnings: [],
                waveforms: [
                  {
                    channel_id: "awg-1/outputs/i",
                    instrument_id: "awg-1",
                    peak_abs: 0.5,
                    rms: 0.25,
                    source_sample_count: 4,
                    samples_sha256: "samples",
                    sample_indices: [0, 1, 2, 3],
                    samples: [0, 0.5, -0.5, 0],
                    downsampling: "none",
                  },
                ],
              },
            ],
          },
        },
      ],
      point: {
        point_index: 0,
        coordinates: { beta: { value: 0, unit: "ns" }, amplification: 1 },
        proposal_fingerprint: "sha256:proposal",
        source: "author",
      },
    },
    pending_request_count: 0,
    planned_points: [
      {
        point_index: 0,
        coordinates: { beta: { value: 0, unit: "ns" }, amplification: 1 },
        source: "author",
      },
    ],
    planned_points_truncated: false,
    session_id: "review-1",
    title: "DRAG beta review",
    updated_at: "2026-08-13T09:00:01Z",
  };
}

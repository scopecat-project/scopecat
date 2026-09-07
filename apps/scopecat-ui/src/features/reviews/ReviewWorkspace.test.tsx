// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
  it("shows typed waveform inspection and physical realization identity", async () => {
    renderWorkspace();

    expect(await screen.findByText("Compile point")).toBeVisible();
    expect(screen.getByText("Planned point #1")).toBeVisible();
    expect(screen.getByRole("img", { name: "Compiled physical waveforms" })).toBeVisible();
    expect(screen.getByText("awg-1/outputs/i")).toBeVisible();
    expect(screen.getByText("sha256:realization")).toBeVisible();
    expect(screen.getByText("Quantum program")).toBeVisible();
    expect(screen.getByLabelText("Program layer overview")).toBeVisible();
    expect(screen.getByText("Intent")).toBeVisible();
    expect(screen.getByText("Timed events")).toBeVisible();
    expect(screen.getByRole("tab", { name: /Scheduled pulse events/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("play drive(q0)")).toBeVisible();
    fireEvent.click(screen.getByText("play drive(q0)"));
    expect(screen.getByText("entity q0")).toBeVisible();
  });

  it("navigates lowering lineage across already loaded abstraction layers", async () => {
    renderWorkspace();
    await screen.findByText("Quantum program");

    fireEvent.click(screen.getByText("play drive(q0)"));
    fireEvent.click(
      screen.getByRole("button", {
        name: "Open lineage Bound logical program x90(q0)",
      }),
    );

    expect(screen.getByRole("tab", { name: /Bound logical program/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("button", { name: /x90\(q0\)/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "← Back to play drive(q0)" })).toBeVisible();
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
    await screen.findByText("Quantum program");
    fireEvent.click(screen.getByText("play drive(q0)"));
    fireEvent.click(
      screen.getByRole("button", {
        name: "Open lineage Bound logical program logical:operation:body/gate",
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
    await screen.findByText("Quantum program");
    fireEvent.click(screen.getByRole("tab", { name: /Physical placement/ }));

    expect(screen.getByText("selected", { selector: "span" })).toBeVisible();
    expect(screen.getByText("rejected", { selector: "span" })).toBeVisible();
    fireEvent.click(screen.getByText("rejected drive(q0) → awg-2:q0"));
    expect(screen.getByText("Route rejected")).toBeVisible();
    expect(screen.getByText("requested q0 but route is configured for q1")).toBeVisible();

    fireEvent.click(screen.getByText("selected drive(q0) → awg-1:q0"));
    expect(screen.getByText("Route selected")).toBeVisible();
  });

  it("queries one bounded program layer without changing the selected point", async () => {
    renderWorkspace();
    await screen.findByText("Quantum program");

    fireEvent.change(screen.getByRole("searchbox", { name: "Program node search" }), {
      target: { value: "drive" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Program entity" }), {
      target: { value: "q0" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Program result" }), {
      target: { value: "readout" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Query layer" }));

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
    await screen.findByText("Quantum program");

    fireEvent.click(screen.getByRole("button", { name: "Next" }));

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
    await screen.findByText("Compile point");

    fireEvent.click(screen.getByRole("button", { name: "free" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: /beta/i }), {
      target: { value: "0.137" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: "amplification" }), {
      target: { value: "2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Compile waveform" }));

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
      await screen.findByText("Compile point");

      fireEvent.click(screen.getByRole("button", { name: mode }));
      fireEvent.click(screen.getByRole("button", { name: "Compile waveform" }));

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
  return render(
    <QueryClientProvider client={client}>
      <ReviewWorkspace daemonUnavailable={false} />
    </QueryClientProvider>,
  );
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

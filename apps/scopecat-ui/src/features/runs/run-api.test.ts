import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getMeasurementLivePreview,
  getMeasurementPreview,
  getMeasurementSlice,
  getMeasurementTracePreview,
  getOlderRuns,
  getRun,
  getRunAnalysisSummaries,
  getRunArtifactDownload,
  getRunContent,
  getRunContents,
  getRunEvents,
  getRunExecutionSegments,
  getRuns,
} from "./run-api";
import type { MeasurementRecord } from "../../api-contract";
import { requestPath } from "../../test/http";
import type { ContentEntry } from "../../types";

const liveMeasurementFixture = readFileSync(
  new URL("./test-fixtures/measurement-append-v11.arrow", import.meta.url),
);

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("run daemon reads", () => {
  it("loads durable execution boundaries for one run", async () => {
    const fetchMock = vi.fn((_input: string | URL | Request) =>
      Promise.resolve(
        jsonResponse({
          items: [
            {
              sequence: 9,
              segment_id: "segment-2",
              run_id: "run/1",
              ordinal: 1,
              executor_id: "executor-after-restart",
              run_contract_fingerprint: "a".repeat(64),
              started_at: "2026-08-23T04:00:00Z",
              start_point_count: 12,
            },
          ],
          next_cursor: null,
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const page = await getRunExecutionSegments("run/1");

    expect(page.items[0]).toMatchObject({
      ordinal: 1,
      start_point_count: 12,
      executor_id: "executor-after-restart",
    });
    expect(requestPath(fetchMock.mock.calls[0]![0])).toBe(
      "/api/v1/runs/run%2F1/execution-segments?limit=100",
    );
  });

  it("loads exact analysis artifact bytes for browser download", async () => {
    const fetchMock = vi.fn((_input: string | URL | Request) =>
      Promise.resolve(
        jsonResponse({
          run_id: "run/1",
          artifact: {
            role: "artifact",
            id: "analysis-fit-r1-fit-report",
            kind: "analysis_artifact",
            filename: "fit-report.md",
            media_type: "text/markdown",
            content_hash: `sha256:${"b".repeat(64)}`,
          },
          content_base64: btoa("# Fit report\n"),
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const download = await getRunArtifactDownload("run/1", "analysis-fit-r1-fit-report");

    expect(download.filename).toBe("fit-report.md");
    expect(download.blob.type).toBe("text/markdown");
    await expect(download.blob.text()).resolves.toBe("# Fit report\n");
    expect(requestPath(fetchMock.mock.calls[0]![0])).toBe(
      "/api/v1/runs/run%2F1/artifacts/analysis-fit-r1-fit-report/bytes" +
        "?expected_kind=analysis_artifact",
    );
  });

  it("loads run detail without eagerly reading its content catalog", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse({
            control: {
              state: "closed",
              completed_point_count: 1,
              point_plan: staticPointPlan("run/1"),
              admission: {
                run_id: "run/1",
                display_name: "Ramsey calibration",
                tags: ["calibration"],
                description: "Calibrate the Ramsey sequence.",
                plan: {
                  experiment_id: "ramsey",
                  experiment_kind: "scratch",
                  point_count: 1,
                  initial_point_count: 1,
                  point_limit: 1,
                  coordinates: [],
                  sampled_points: [],
                  sampled_points_truncated: false,
                },
              },
            },
            snapshot: {
              run_id: "run/1",
              config_content_hash: "sha256:config",
              outcome: { result: "succeeded", certainty: "known" },
            },
            resources: [],
          }),
        ),
      ),
    );

    const run = await getRun("run/1");

    expect(run).toMatchObject({
      experimentId: "ramsey",
      displayName: "Ramsey calibration",
      tags: ["calibration"],
      description: "Calibrate the Ramsey sequence.",
      status: "succeeded",
      result: "succeeded",
      certainty: "known",
    });
    expect(run.contents).toEqual([]);
  });

  it("loads one bounded page of run contents", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse({
            run_id: "run/1",
            items: [
              {
                role: "artifact",
                id: "fit-notes",
                kind: "attachment",
                title: "Fit notes",
                media_type: "text/markdown",
                filename: "fit.md",
                content_hash: "sha256:notes",
              },
              {
                role: "record",
                id: "analysis-fit-r1",
                kind: "analysis",
                content_hash: "sha256:analysis",
              },
            ],
            next_cursor: 17,
          }),
        ),
      ),
    );

    const page = await getRunContents("run/1");

    expect(page.nextCursor).toBe(17);
    expect(page.items).toEqual([
      {
        id: "fit-notes",
        role: "artifact",
        kind: "attachment",
        label: "Fit notes",
        detail: "text/markdown",
        mediaType: "text/markdown",
        filename: "fit.md",
      },
      {
        id: "analysis-fit-r1",
        role: "record",
        kind: "analysis",
        label: "Record 2",
        detail: "analysis",
        mediaType: undefined,
        filename: undefined,
      },
    ]);
  });

  it("prioritizes scheduler attention over a terminal outcome", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string | URL | Request) =>
        Promise.resolve(
          jsonResponse(
            requestPath(input).includes("/contents?")
              ? {
                  run_id: "run/attention",
                  items: [],
                  next_cursor: null,
                }
              : {
                  control: {
                    state: "attention_required",
                    completed_point_count: 0,
                    point_plan: staticPointPlan("run/attention"),
                    attention_reason: "executor_lease_expired",
                    admission: {
                      run_id: "run/attention",
                      plan: {
                        experiment_id: "ramsey",
                        experiment_kind: "scratch",
                        point_count: 1,
                        initial_point_count: 1,
                        point_limit: 1,
                        coordinates: [],
                        sampled_points: [],
                        sampled_points_truncated: false,
                      },
                    },
                  },
                  snapshot: {
                    run_id: "run/attention",
                    config_content_hash: "sha256:config",
                    outcome: { result: "failed", certainty: "indeterminate" },
                  },
                  resources: [],
                },
          ),
        ),
      ),
    );

    await expect(getRun("run/attention")).resolves.toMatchObject({
      status: "attention_required",
      attentionReason: "executor_lease_expired",
    });
  });

  it("derives nonterminal status from scheduler ownership", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse({
            items: [runSummary("run/queued", "queued"), runSummary("run/leased", "leased")],
            next_cursor: null,
          }),
        ),
      ),
    );

    await expect(getRuns()).resolves.toMatchObject({
      items: [
        { runId: "run/leased", status: "running" },
        { runId: "run/queued", status: "accepted" },
      ],
    });
  });

  it("reads persisted analyses and typed run content", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const path = requestPath(input);
      if (path.includes("/analyses?")) {
        return Promise.resolve(
          jsonResponse({
            run_id: "run/1",
            items: [
              {
                entry: { id: "analysis-fit-r1" },
                title: "Fit review",
                key: "fit",
                revision: 1,
                publication_hash: "sha256:analysis",
                published_at: "2026-08-17T12:00:00Z",
                input_count: 1,
                output_count: 1,
                analysis: {
                  title: "Fit review",
                  key: "fit",
                  inputs: [
                    {
                      target: "measurement-dataset",
                      kind: "measurement_dataset",
                      role: "fit-input",
                      title: "Sweep data",
                      metadata: { selector: "raw-measurements" },
                    },
                  ],
                  executions: [
                    {
                      id: "fit",
                      implementation: "python:fit",
                      deterministic: false,
                      inputs: ["dataset"],
                      input_bindings: [
                        {
                          name: "dataset",
                          kind: "measurement_dataset",
                          target: "measurement-dataset",
                          content_hash: "sha256:measurements",
                          codec: "scopecat.measurement-dataset.v8",
                        },
                      ],
                      outputs: [
                        {
                          name: "fit",
                          kind: "value",
                          content_hash: "sha256:fitted-frequency",
                          codec: "scopecat.python-json.v1",
                        },
                      ],
                      captures: [],
                      access: "full",
                      metadata: {},
                    },
                  ],
                  outputs: [
                    {
                      kind: "fact",
                      id: "fitted-frequency",
                      title: "Fitted frequency",
                      produced_by: {
                        execution_id: "fit",
                        output_name: "fit",
                      },
                      content: {
                        schema_id: "scopecat.scalar.v1",
                        schema_codec: "scopecat.analysis-fact-schema.v1",
                        schema_hash: `sha256:${"c".repeat(64)}`,
                        codec: "scopecat.python-json.v1",
                        value: 5.1,
                      },
                    },
                  ],
                },
              },
            ],
          }),
        );
      }
      if (path.includes("/artifacts/")) {
        if (path.includes("/json")) {
          return Promise.resolve(
            jsonResponse({
              artifact: jsonArtifact(),
              content: { converged: true },
            }),
          );
        }
        return Promise.resolve(
          jsonResponse({
            artifact: textArtifact(),
            content: "Converged\n",
          }),
        );
      }
      return Promise.resolve(
        jsonResponse({
          record: {
            role: "record",
            id: "analysis-fit-r1",
            kind: "analysis",
            content_hash: "sha256:analysis",
          },
          content: { title: "Fit review" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const analyses = await getRunAnalysisSummaries("run/1");
    const text = await getRunContent("run/1", textArtifact());
    const json = await getRunContent("run/1", jsonArtifact());
    const record = await getRunContent("run/1", {
      id: "analysis-fit-r1",
      role: "record",
      kind: "analysis",
      label: "Fit review",
      detail: "analysis",
    });

    expect(analyses.items[0]).toMatchObject({
      id: "analysis-fit-r1",
      title: "Fit review",
      key: "fit",
      inputCount: 1,
      outputCount: 1,
      publishedAt: "2026-08-17T12:00:00Z",
    });
    expect(text).toMatchObject({ format: "text", content: "Converged\n" });
    expect(json).toMatchObject({
      format: "json",
      content: { converged: true },
    });
    expect(record).toMatchObject({
      format: "json",
      content: { title: "Fit review" },
    });
    expect(fetchMock.mock.calls.map(([path]) => requestPath(path))).toEqual([
      "/api/v1/runs/run%2F1/analyses?limit=100",
      "/api/v1/runs/run%2F1/artifacts/fit-notes/text?expected_kind=attachment",
      "/api/v1/runs/run%2F1/artifacts/fit-result/json?expected_kind=result",
      "/api/v1/runs/run%2F1/records/analysis-fit-r1/json?expected_kind=analysis",
    ]);
  });

  it("uses the daemon's backward run cursor and run-scoped event query", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const path = requestPath(input);
      if (path.includes("/events?")) {
        return Promise.resolve(
          jsonResponse({
            items: [
              {
                event_id: 12,
                run_id: "run/1",
                kind: "run_state_changed",
                payload: { state: "running" },
              },
            ],
          }),
        );
      }
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              control: {
                sequence: path.includes("before=") ? 1 : 2,
                state: "queued",
                completed_point_count: 0,
                point_plan: staticPointPlan(path.includes("before=") ? "run-old" : "run-new"),
                admission: {
                  run_id: path.includes("before=") ? "run-old" : "run-new",
                  plan: {
                    experiment_id: "ramsey",
                    experiment_kind: "scratch",
                    point_count: 1,
                    initial_point_count: 1,
                    point_limit: 1,
                    coordinates: [],
                    sampled_points: [],
                    sampled_points_truncated: false,
                  },
                },
              },
              snapshot: {
                run_id: path.includes("before=") ? "run-old" : "run-new",
                config_content_hash: "sha256:config",
              },
            },
          ],
          next_cursor: path.includes("before=") ? null : 2,
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(getRuns()).resolves.toMatchObject({
      items: [{ runId: "run-new", sequence: 2 }],
      nextCursor: 2,
    });
    await expect(getOlderRuns(2)).resolves.toMatchObject({
      items: [{ runId: "run-old", sequence: 1 }],
      nextCursor: undefined,
    });
    await expect(getRunEvents("run/1")).resolves.toMatchObject([
      { id: 12, runId: "run/1", kind: "run_state_changed" },
    ]);
    expect(fetchMock.mock.calls.map(([path]) => requestPath(path))).toEqual([
      "/api/v1/runs?limit=100",
      "/api/v1/runs?limit=100&before=2",
      "/api/v1/events?limit=500&latest=true&run_id=run%2F1",
    ]);
  });

  it("requests one bounded measurement preview", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL) =>
      Promise.resolve(
        jsonResponse({
          items: [measurementRecord("run/1", 100)],
          dataset_schema: measurementSchema(),
          truncated: true,
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(getMeasurementPreview("run/1")).resolves.toEqual({
      items: [measurementRecord("run/1", 100)],
      schema: measurementSchema(),
      truncated: true,
    });
    expect(requestPath(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/runs/run%2F1/measurements/preview?limit=100",
    );
  });

  it("reads the latest daemon-received measurement without forcing a flush", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL) =>
      Promise.resolve(liveArrowResponse(8, 0)),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(getMeasurementLivePreview("run/1")).resolves.toMatchObject({
      active: true,
      latest: {
        run_id: "run-arrow-v10",
        logical_point_id: "point-7",
        point_index: 7,
      },
      receivedRecordCount: 8,
      durableRecordCount: 0,
    });
    expect(requestPath(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/runs/run%2F1/measurements/live",
    );
  });

  it("requests one semantic product-grid slice", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL) =>
      Promise.resolve(
        jsonResponse({
          items: [measurementRecord("run/1", 3)],
          dataset_schema: measurementSchema(),
          selected_point_count: 6,
          offset: 4,
          window_point_count: 1,
          next_offset: 5,
          previous_offset: 0,
          truncated: false,
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      getMeasurementSlice("run/1", { bias: 1 }, ["x", "y", "signal"], 4),
    ).resolves.toEqual({
      items: [measurementRecord("run/1", 3)],
      schema: measurementSchema(),
      selectedPointCount: 6,
      offset: 4,
      windowPointCount: 1,
      nextOffset: 5,
      previousOffset: 0,
      truncated: false,
    });
    const request = fetchMock.mock.calls[0]?.[0];
    expect(requestPath(request)).toBe("/api/v1/runs/run%2F1/measurements/query");
    expect(request).toBeInstanceOf(Request);
    await expect((request as Request).clone().json()).resolves.toEqual({
      fixed_axis_indices: { bias: 1 },
      include_schema: false,
      limit: 4096,
      offset: 4,
      variable_ids: ["x", "y", "signal"],
    });
  });

  it("requests one bounded response-ready trace preview", async () => {
    const response = {
      coordinate_id: "frequency",
      dimension_id: "sample",
      downsampling: "minmax" as const,
      layout: "overlay" as const,
      fixed_axis_indices: { bias: 1 },
      observable_id: "signal",
      recording_group_id: "readout",
      returned_sample_count: 2,
      returned_series_count: 1,
      inspected_series_count: 1,
      failures: [],
      samples_reduced: true,
      selected_series_count: 6,
      series: [
        {
          label: "Point 3",
          point_index: 3,
          source_sample_count: 100,
          available_sample_count: 100,
          unavailable_reasons: [],
          x: [4.9, 5.1],
          y: [0.2, 0.3],
        },
      ],
      source_sample_count: 100,
      truncated_series: true,
      value_mode: "magnitude" as const,
    };
    const fetchMock = vi.fn((_input: RequestInfo | URL) => Promise.resolve(jsonResponse(response)));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      getMeasurementTracePreview("run/1", {
        observableId: "signal",
        coordinateId: "frequency",
        fixedAxisIndices: { bias: 1 },
        valueMode: "magnitude",
        entities: [
          { kind: "qubit", id: "q1" },
          { kind: "qubit", id: "q3" },
        ],
      }),
    ).resolves.toEqual(response);
    const request = fetchMock.mock.calls[0]?.[0];
    expect(requestPath(request)).toBe("/api/v1/runs/run%2F1/measurements/traces/query");
    expect(request).toBeInstanceOf(Request);
    await expect((request as Request).clone().json()).resolves.toEqual({
      coordinate_id: "frequency",
      downsampling: "minmax",
      fixed_axis_indices: { bias: 1 },
      entities: [
        { kind: "qubit", id: "q1" },
        { kind: "qubit", id: "q3" },
      ],
      max_samples: 4096,
      max_series: 32,
      observable_id: "signal",
      value_mode: "magnitude",
    });
  });
});

function measurementSchema() {
  return {
    format_version: "scopecat.measurement_dataset_schema.v17" as const,
    dataset_id: "raw-measurements",
    record_schema: "scopecat.measurement_record.v10" as const,
    point_domain: { kind: "product_grid" as const, axes: [] },
    dimensions: [{ id: "point", kind: "point", size: 1 }],
    variables: [],
  };
}

function measurementRecord(runId: string, pointIndex: number): MeasurementRecord {
  return {
    run_id: runId,
    logical_point_id: `point-${pointIndex}`,
    point_index: pointIndex,
    coordinates: {},
    observables: {},
  };
}

function runSummary(runId: string, state: "queued" | "leased") {
  return {
    control: {
      sequence: state === "leased" ? 2 : 1,
      state,
      completed_point_count: 0,
      point_plan: staticPointPlan(runId),
      admission: {
        run_id: runId,
        plan: {
          experiment_id: "ramsey",
          experiment_kind: "scratch",
          point_count: 1,
          initial_point_count: 1,
          point_limit: 1,
          coordinates: [],
          sampled_points: [],
          sampled_points_truncated: false,
        },
      },
    },
    snapshot: {
      run_id: runId,
      config_content_hash: "sha256:config",
    },
  };
}

function staticPointPlan(runId: string) {
  return {
    run_id: runId,
    initial_point_count: 1,
    accepted_point_count: 1,
    point_limit: 1,
    decision_count: 0,
    optimizer_attempt_count: 0,
    operator_request_count: 0,
    plan_closed: true,
    stop_reason: "static point plan",
  };
}

function textArtifact(): ContentEntry {
  return {
    id: "fit-notes",
    role: "artifact",
    kind: "attachment",
    label: "Fit notes",
    detail: "text/markdown",
    mediaType: "text/markdown",
    filename: "fit.md",
  };
}

function jsonArtifact(): ContentEntry {
  return {
    id: "fit-result",
    role: "artifact",
    kind: "result",
    label: "Fit result",
    detail: "application/json",
    mediaType: "application/json",
    filename: "fit.json",
  };
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function liveArrowResponse(receivedRecordCount: number, durableRecordCount: number): Response {
  const content = liveMeasurementFixture.buffer.slice(
    liveMeasurementFixture.byteOffset,
    liveMeasurementFixture.byteOffset + liveMeasurementFixture.byteLength,
  );
  return new Response(content, {
    status: 200,
    headers: {
      "Content-Type": "application/vnd.apache.arrow.file",
      "X-Scopecat-Measurement-Active": "true",
      "X-Scopecat-Received-Record-Count": String(receivedRecordCount),
      "X-Scopecat-Durable-Record-Count": String(durableRecordCount),
    },
  });
}

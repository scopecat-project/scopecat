// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MeasurementDataPreview } from "./MeasurementDataPreview";
import {
  measurementTraceChart,
  measurementTraceQueryPlans,
  measurementTraceStatus,
} from "./measurement-visualization";
import {
  entityTraceSchema,
  realTraceSchema,
  tracePreview,
  traceSchema,
  traceSeries,
} from "./measurement-trace.test-support";

vi.mock("../../ui/EChartRuntime", () => ({ EChartRuntime: () => null }));

afterEach(cleanup);

describe("measurement trace visualization", () => {
  it("derives explicit server trace queries from one compatible recording group", () => {
    expect(measurementTraceQueryPlans(traceSchema())).toEqual([
      {
        id: "trace:response:frequency:magnitude",
        label: "S21 magnitude",
        observableId: "response",
        coordinateId: "frequency",
        valueMode: "magnitude",
      },
      expect.objectContaining({
        id: "trace:response:frequency:phase",
        valueMode: "phase",
      }),
      expect.objectContaining({
        id: "trace:response:frequency:real",
        valueMode: "real",
      }),
      expect.objectContaining({
        id: "trace:response:frequency:imag",
        valueMode: "imag",
      }),
    ]);
  });

  it("uses the same value mode for a real trace query and label", () => {
    expect(measurementTraceQueryPlans(realTraceSchema())).toEqual([
      {
        id: "trace:response:frequency:value",
        label: "S21",
        observableId: "response",
        coordinateId: "frequency",
        valueMode: "value",
      },
    ]);
  });

  it("derives one server trace plan over an indexed entity axis", () => {
    expect(measurementTraceQueryPlans(entityTraceSchema())[0]).toMatchObject({
      id: "trace:response:frequency:magnitude",
      entityAxisId: "qubit",
      observableId: "response",
      coordinateId: "frequency",
    });
  });

  it("omits a trace pair when only one variable has a recording group", () => {
    const source = traceSchema();
    const schema = {
      ...source,
      variables: source.variables?.map((variable) =>
        variable.id === "frequency" ? { ...variable, recording_group_id: undefined } : variable,
      ),
    };

    expect(measurementTraceQueryPlans(schema)).toEqual([]);
  });

  it("synthesizes sample indices when only unrelated trace coordinates exist", () => {
    const source = traceSchema();
    const schema = {
      ...source,
      dimensions: [...source.dimensions, { id: "monitor_sample", kind: "sample", size: 2 }],
      variables: source.variables?.map((variable) =>
        variable.id === "frequency"
          ? {
              ...variable,
              dims: ["point", "monitor_sample"],
              recording_group_id: "monitor",
            }
          : variable,
      ),
    };

    expect(measurementTraceQueryPlans(schema)[0]).toEqual({
      id: "trace:response:sample:magnitude",
      label: "S21 magnitude",
      observableId: "response",
      valueMode: "magnitude",
    });
  });

  it("lists each explicit trace coordinate when several pairs are safe", () => {
    const source = traceSchema();
    const coordinate = source.variables?.find((variable) => variable.id === "frequency");
    const observable = source.variables?.find((variable) => variable.id === "response");
    if (!coordinate || !observable) throw new Error("trace fixture variables are incomplete");
    const schema = {
      ...source,
      variables: [
        ...(source.variables ?? []).map((variable) => ({
          ...variable,
          recording_group_id: undefined,
        })),
        {
          ...coordinate,
          id: "time",
          label: "Time",
          unit: "ns",
          recording_group_id: undefined,
        },
      ],
    };

    const plans = measurementTraceQueryPlans(schema);

    expect(plans).toHaveLength(8);
    expect(plans[0]).toMatchObject({
      coordinateId: "frequency",
      label: "S21 magnitude by Frequency",
    });
    expect(plans[4]).toMatchObject({
      coordinateId: "time",
      label: "S21 magnitude by Time",
    });
  });

  it("maps response-ready numeric series without reprocessing samples", () => {
    const chart = measurementTraceChart(
      tracePreview({
        value_mode: "phase",
        value_unit: "rad",
        series: [
          traceSeries(0, [4, 5, 6], [Math.atan2(4, 3), Math.PI / 2, 0], 300),
          traceSeries(1, [4, 6], [0.2, 0.4], 200),
        ],
        selected_series_count: 2,
        returned_series_count: 2,
        source_sample_count: 500,
        returned_sample_count: 5,
        samples_reduced: true,
      }),
    );

    expect(chart).toMatchObject({
      id: "trace:response:frequency:phase",
      kind: "line",
      title: "S21 phase",
      xLabel: "Frequency [GHz]",
      yLabel: "phase(S21) [rad]",
      note: "Complex values are shown as phase in radians.",
    });
    expect(chart?.series[0]?.points).toEqual([
      { x: 4, y: Math.atan2(4, 3) },
      { x: 5, y: Math.PI / 2 },
      { x: 6, y: 0 },
    ]);
  });

  it("uses the effective response mode for real trace labels", () => {
    const chart = measurementTraceChart(
      tracePreview({
        value_mode: "value",
        value_unit: "ratio",
        series: [traceSeries(0, [4, 5], [-2, 3])],
        selected_series_count: 1,
        returned_series_count: 1,
        source_sample_count: 2,
        returned_sample_count: 2,
      }),
    );

    expect(chart).toMatchObject({
      title: "S21",
      yLabel: "S21 [ratio]",
      note: undefined,
    });
  });

  it("renders a server series with a non-monotonic coordinate as scatter", () => {
    const chart = measurementTraceChart(
      tracePreview({
        series: [traceSeries(0, [0, 2, 1], [1, 2, 3])],
        selected_series_count: 1,
        returned_series_count: 1,
        source_sample_count: 3,
        returned_sample_count: 3,
      }),
    );

    expect(chart).toMatchObject({ kind: "scatter" });
  });

  it("selects a bounded trace mode and renders server series with limit status", () => {
    const schema = traceSchema();
    const plans = measurementTraceQueryPlans(schema);
    const onTracePlanChange = vi.fn();
    const preview = tracePreview({
      series: [traceSeries(0, [4, 5, 6], [5, 2, 1], 60), traceSeries(1, [4, 6], [4, 3], 40)],
      selected_series_count: 8,
      returned_series_count: 2,
      truncated_series: true,
      source_sample_count: 100,
      returned_sample_count: 5,
      samples_reduced: true,
    });

    render(
      <MeasurementDataPreview
        preview={{ schema, items: [] }}
        slice={{
          items: [],
          schema,
          selectedPointCount: 0,
          offset: 0,
          windowPointCount: 0,
          truncated: false,
        }}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{}}
        onFixedAxisIndexChange={vi.fn()}
        tracePlans={plans}
        selectedTracePlanId={plans[0]!.id}
        tracePreview={preview}
        tracePending={false}
        traceError={null}
        onTracePlanChange={onTracePlanChange}
      />,
    );

    const selector = screen.getByRole("combobox", { name: "Measurement trace" });
    expect(selector).toHaveValue("trace:response:frequency:magnitude");
    expect(screen.getByRole("option", { name: "S21 magnitude" })).toBeVisible();
    expect(screen.getByRole("option", { name: "S21 phase" })).toBeVisible();
    fireEvent.change(selector, { target: { value: "trace:response:frequency:phase" } });
    expect(onTracePlanChange).toHaveBeenCalledWith("trace:response:frequency:phase");
    expect(
      screen.getByRole("img", { name: "S21 magnitude: |S21| [ratio] by Frequency [GHz]" }),
    ).toBeVisible();
    expect(
      screen.getByText(
        "2 条已绘制 · 已检查 2/8 条选定序列 · 已应用序列数量上限 · 已绘制 5/100 个原始采样点 · 已略去不可用采样点或采用最小值/最大值降采样",
      ),
    ).toBeVisible();
  });

  it("shows bounded trace pending and error states without stale charts", () => {
    const schema = traceSchema();
    const plans = measurementTraceQueryPlans(schema);
    const stalePreview = tracePreview({
      entity_acquisition: { policy: "all_or_nothing", cohort_id: "stale-cohort" },
      failures: [
        {
          point_index: 0,
          logical_point_id: "point-0",
          label: "stale Q1",
          reasons: ["overload"],
        },
      ],
      selected_series_count: 2,
      inspected_series_count: 2,
    });
    const common = {
      preview: { schema, items: [] },
      slice: {
        items: [],
        schema,
        selectedPointCount: 0,
        offset: 0,
        windowPointCount: 0,
        truncated: false,
      },
      sliceError: null,
      slicePending: false,
      fixedAxisIndices: {},
      onFixedAxisIndexChange: vi.fn(),
      tracePlans: plans,
      selectedTracePlanId: plans[0]!.id,
      hasMore: false,
      loadingMore: false,
      onLoadMore: vi.fn(),
    };
    const view = render(
      <MeasurementDataPreview
        {...common}
        tracePreview={stalePreview}
        tracePending
        traceError={null}
      />,
    );

    expect(screen.getByText("正在读取有界波形预览…")).toBeVisible();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByText("不可用 · overload")).not.toBeInTheDocument();
    expect(screen.queryByText(/stale-cohort/)).not.toBeInTheDocument();

    view.rerender(
      <MeasurementDataPreview
        {...common}
        tracePreview={stalePreview}
        tracePending={false}
        traceError={new Error("offline")}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("波形不可用： offline");
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByText("不可用 · overload")).not.toBeInTheDocument();
    expect(screen.queryByText(/stale-cohort/)).not.toBeInTheDocument();
  });

  it("formats trace status without claiming an absent reduction", () => {
    expect(measurementTraceStatus(tracePreview())).toBe(
      "1 条已绘制 · 已检查 1/1 条选定序列 · 3 个原始采样点已绘制",
    );
  });

  it("filters entity trace queries and exposes failure acquisition evidence", async () => {
    const schema = entityTraceSchema();
    const plans = measurementTraceQueryPlans(schema);
    const onEntitySelectionChange = vi.fn();
    const evidence = {
      command_id: "collect-q1",
      instrument_id: "readout",
      interface_id: "test.readout/v1",
      component_path: ["channel-b"],
      acquisition_id: "sample",
      result_id: "q1-response",
      started_at: "2026-08-15T10:00:00Z",
      completed_at: "2026-08-15T10:00:00.002Z",
    };
    const preview = tracePreview({
      entity_dimension_id: "qubit",
      entity_acquisition: { policy: "all_or_nothing", cohort_id: "readout-cohort" },
      layout: "small_multiples",
      series: [
        {
          ...traceSeries(0, [4, 6], [1, 3], 3),
          label: "Delay 88 ns · Q0",
          entity_index: 0,
          entity: { id: "q0", kind: "qubit", metadata: { label: "Q0" } },
          available_sample_count: 2,
          unavailable_reasons: ["overload"],
        },
      ],
      failures: [
        {
          point_index: 0,
          logical_point_id: "point-0",
          label: "Delay 88 ns · Q1",
          entity_index: 1,
          entity: { id: "q1", kind: "qubit", metadata: { label: "Q1" } },
          reasons: ["overload"],
          evidence,
        },
      ],
      selected_series_count: 2,
      inspected_series_count: 2,
      returned_series_count: 1,
      source_sample_count: 3,
      returned_sample_count: 2,
      samples_reduced: true,
    });

    render(
      <MeasurementDataPreview
        preview={{ schema, items: [] }}
        sliceError={null}
        slicePending={false}
        fixedAxisIndices={{}}
        onFixedAxisIndexChange={vi.fn()}
        tracePlans={plans}
        selectedTracePlanId={plans[0]!.id}
        tracePreview={preview}
        onEntitySelectionChange={onEntitySelectionChange}
      />,
    );

    expect(screen.getByText("all or nothing · cohort readout-cohort")).toBeVisible();
    expect(screen.getByText("small multiples")).toBeVisible();
    expect(screen.getByText("不可用 · overload")).toBeVisible();
    expect(screen.getByText("2/3 个采样点可用 · overload")).toBeVisible();
    fireEvent.click(screen.getByText("Delay 88 ns · Q1"));
    expect(screen.getByText("readout · sample → q1-response")).toBeVisible();
    expect(screen.getByText("channel-b")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Qubits Q1" }));
    await waitFor(() => expect(onEntitySelectionChange).toHaveBeenLastCalledWith({ qubit: [1] }));
  });
});

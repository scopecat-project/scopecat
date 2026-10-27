import { Notice, Button, Field, Select } from "../../ui/Controls";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { MeasurementRecordTable } from "../runs/MeasurementRecordTable";
import { measurementTable, planMeasurementCharts } from "../runs/measurement-visualization";
import { MeasurementChartPicker } from "../runs/MeasurementDataPreview";
import { CaptureAnalyses } from "./CaptureAnalyses";
import { CaptureTraces } from "./CaptureTraces";
import { WindowTitle } from "../application/WindowTitle";
import { selectCapture, useCaptureSelection, type CaptureSelection } from "./capture-location";

export function CaptureDetail({ contentHash }: { contentHash: string }) {
  const selection = useCaptureSelection(contentHash);
  const selectRun = (runId?: string) =>
    selectCapture(contentHash, { runId, measurements: "acquired", offset: 0 });
  const evidence = useQuery({
    queryKey: ["data", "capture", contentHash],
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/data/captures/{content_hash}/evidence", {
          params: { path: { content_hash: contentHash } },
          signal,
        }),
      ),
  });
  if (evidence.error) return <Notice role="alert">{evidence.error.message}</Notice>;
  if (!evidence.data) return <Notice role="status">正在加载数据…</Notice>;
  const capture = evidence.data;
  const run = capture.runs.find(
    (item) => item.snapshot.run_id === (selection.runId ?? capture.roots[0]),
  );
  if (!run)
    return (
      <div>
        <Notice role="alert">此文件中缺少引用的运行。</Notice>
        <Button onClick={() => selectRun()}>返回文件的入口运行</Button>
      </div>
    );
  return (
    <section className="mt-3 space-y-3" aria-label="文件中的运行">
      <WindowTitle title={`${run.snapshot.run_id} · ${capture.source_project_id} · Scopecat`} />
      <Field>
        运行{" "}
        <Select value={run.snapshot.run_id} onChange={(event) => selectRun(event.target.value)}>
          {capture.runs.map((item) => (
            <option key={item.snapshot.run_id} value={item.snapshot.run_id}>
              {item.request.display_name ?? item.request.experiment_id ?? item.snapshot.run_id} ·{" "}
              {item.snapshot.run_id}
            </option>
          ))}
        </Select>
      </Field>
      <h4 className="font-semibold">
        {run.request.display_name ?? run.request.experiment_id ?? run.snapshot.run_id}
      </h4>
      <p>运行：{run.snapshot.run_id}</p>
      <p>来源：{capture.source_project_id}</p>
      <CaptureRecording
        key={run.snapshot.run_id}
        contentHash={contentHash}
        runId={run.snapshot.run_id}
        selection={selection}
      />
      <details>
        <summary>保留的请求与配置</summary>
        <pre className="max-h-80 overflow-auto text-xs">
          {JSON.stringify({ request: run.request, configuration: run.configuration }, null, 2)}
        </pre>
      </details>
      <CaptureAnalyses
        contentHash={contentHash}
        analyses={capture.analyses ?? []}
        onOpenRun={selectRun}
      />
    </section>
  );
}

function CaptureRecording({
  contentHash,
  runId,
  selection: view,
}: {
  contentHash: string;
  runId: string;
  selection: CaptureSelection;
}) {
  const { offset, measurements: selection } = view;
  const setOffset = (nextOffset: number) =>
    selectCapture(contentHash, { ...view, runId, offset: nextOffset });
  const page = useQuery({
    queryKey: ["data", "recording", contentHash, runId, selection, offset],
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/data/captures/{content_hash}/runs/{run_id}/recording", {
          params: {
            path: { content_hash: contentHash, run_id: runId },
            query: { selection, offset, limit: 100 },
          },
          signal,
        }),
      ),
  });
  const charts = useMemo(
    () => (page.data ? planMeasurementCharts(page.data.items, page.data.dataset_schema) : []),
    [page.data],
  );
  return (
    <div className="space-y-3">
      <Field>
        测量记录{" "}
        <Select
          value={selection}
          onChange={(event) => {
            selectCapture(contentHash, {
              runId,
              measurements: event.target.value as "acquired" | "selected",
              offset: 0,
            });
          }}
        >
          <option value="acquired">采集历史</option>
          <option value="selected">保留的分析选点</option>
        </Select>
      </Field>
      {page.isPending && <Notice role="status">正在加载测量记录…</Notice>}
      {page.error && <Notice role="alert">{page.error.message}</Notice>}
      {page.data && (
        <>
          <p>
            {page.data.record_count}{" "}
            {selection === "acquired" ? "次采集，按采集顺序排列" : "个选定点，按逻辑点顺序排列"}.
            {page.data.selected_record_count === null && " 未保留分析选点。"}
          </p>
          <MeasurementRecordTable
            table={measurementTable(page.data.items, page.data.dataset_schema)}
          />
          {page.data.items.some((record) =>
            [...Object.values(record.coordinates), ...Object.values(record.observables)].some(
              (value) => value.kind === "array_summary",
            ),
          ) && <p>表格仅显示大型数组摘要，点图不包含这些数组。</p>}
          <CaptureTraces
            key={`${selection}:${offset}`}
            contentHash={contentHash}
            runId={runId}
            selection={selection}
            offset={offset}
            records={page.data.items}
            schema={page.data.dataset_schema}
          />
          {charts.length > 0 && (
            <section aria-label="当前测量页的图表">
              <p>图表仅显示本页的 {page.data.items.length} 条记录。</p>
              <MeasurementChartPicker charts={charts} />
            </section>
          )}
          <div className="flex flex-wrap items-end gap-3">
            <Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>
              上一页测量记录
            </Button>
            <span>
              {page.data.items.length ? offset + 1 : 0}–{offset + page.data.items.length} /{" "}
              {page.data.record_count}
            </span>
            <Button
              disabled={page.data.next_offset === null}
              onClick={() => setOffset(page.data.next_offset!)}
            >
              下一页测量记录
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
import { useMemo } from "react";

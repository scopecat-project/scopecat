import { useMemo, useState } from "react";
import type { AnalysisOutput } from "../../types";
import { EChart } from "../../ui/EChart";
import { analysisAxisLabel, analysisFigureOption } from "./chart-options";

export function AnalysisOutputView({
  output,
  getArtifactDownload,
}: {
  output: AnalysisOutput;
  getArtifactDownload: AnalysisArtifactDownloader;
}) {
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string>();

  async function downloadArtifact() {
    if (output.kind !== "artifact") return;
    setDownloading(true);
    setDownloadError(undefined);
    try {
      const download = await getArtifactDownload(output.content.artifact_id);
      const url = URL.createObjectURL(download.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = download.filename;
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }

  let content;
  if (output.kind === "table") {
    content = <AnalysisTableView content={output.content} title={output.title} />;
  } else if (output.kind === "figure") {
    content = <AnalysisFigureView content={output.content} title={output.title} />;
  } else if (output.kind === "dataset") {
    content = (
      <dl className="m-0 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 p-[9px] text-[0.62rem]">
        <dt className="font-bold text-text-dim">Dataset</dt>
        <dd className="m-0 min-w-0 text-text-soft">
          <code>{output.content.dataset_id}</code>
        </dd>
        <dt className="font-bold text-text-dim">Codec</dt>
        <dd className="m-0 min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-text-soft">
          <code title={output.content.codec}>{output.content.codec}</code>
        </dd>
        <dt className="font-bold text-text-dim">Content</dt>
        <dd className="m-0 min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-text-soft">
          <code title={output.content.content_hash}>{output.content.content_hash}</code>
        </dd>
      </dl>
    );
  } else if (output.kind === "fact") {
    content = (
      <div className="p-[9px] text-[0.62rem] text-text-soft">
        <div className="mb-1 text-[0.58rem] font-bold text-text-dim">
          {output.content.schema_id}
        </div>
        <div className="mb-1 overflow-hidden text-ellipsis whitespace-nowrap text-[0.55rem] text-text-dim">
          <code title={output.content.schema_hash}>{output.content.schema_hash}</code>
        </div>
        <div className="mb-1 overflow-hidden text-ellipsis whitespace-nowrap text-[0.55rem] text-text-dim">
          <code>{output.content.schema_codec}</code>
        </div>
        <pre className="m-0 max-h-36 overflow-auto whitespace-pre-wrap">
          {JSON.stringify(output.content.value, null, 2)}
        </pre>
      </div>
    );
  } else if (output.kind === "artifact") {
    content = (
      <dl className="m-0 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 p-[9px] text-[0.62rem]">
        <dt className="font-bold text-text-dim">Artifact</dt>
        <dd className="m-0 min-w-0 text-text-soft">
          <code>{output.content.artifact_id}</code>
        </dd>
        <dt className="font-bold text-text-dim">File</dt>
        <dd className="m-0 min-w-0 text-text-soft">
          <code>{output.content.filename}</code>
        </dd>
        <dt className="font-bold text-text-dim">Media type</dt>
        <dd className="m-0 min-w-0 text-text-soft">
          <code>{output.content.media_type}</code>
        </dd>
        <dt className="font-bold text-text-dim">Content</dt>
        <dd className="m-0 min-w-0 text-text-soft">
          <button
            className="cursor-pointer rounded border border-line bg-panel-soft px-2 py-1 text-[0.58rem] font-bold text-text-soft hover:border-line-strong disabled:cursor-wait disabled:opacity-60"
            disabled={downloading}
            onClick={downloadArtifact}
            type="button"
          >
            {downloading ? "Downloading…" : "Download file"}
          </button>
          {downloadError ? (
            <span className="ml-2 text-red" role="alert">
              {downloadError}
            </span>
          ) : null}
        </dd>
      </dl>
    );
  } else {
    content = (
      <dl className="m-0 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 p-[9px] text-[0.62rem]">
        <dt className="font-bold text-text-dim">Proposal</dt>
        <dd className="m-0 min-w-0 text-text-soft">
          <code>{output.content.proposal_id}</code>
        </dd>
        <dt className="font-bold text-text-dim">Record</dt>
        <dd className="m-0 min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-text-soft">
          <code title={output.content.record_ref}>{output.content.record_ref}</code>
        </dd>
      </dl>
    );
  }

  return (
    <>
      {content}
      <AnalysisMetadataView metadata={output.metadata} />
    </>
  );
}

export type AnalysisArtifactDownloader = (
  selector: string,
) => Promise<{ blob: Blob; filename: string }>;

type TableContent = Extract<AnalysisOutput, { kind: "table" }>["content"];

function AnalysisTableView({ content, title }: { content: TableContent; title: string }) {
  const preview = content.preview;
  const rows = preview.rows ?? [];
  return (
    <div className="max-h-72 overflow-auto" data-testid="analysis-table">
      <table className="w-full border-collapse text-left text-[0.61rem]" aria-label={title}>
        <thead className="sticky top-0 z-[1] bg-panel-strong text-text-dim">
          <tr>
            {preview.columns.map((column) => (
              <th
                className="border-b border-line px-2.5 py-2 font-extrabold tracking-[0.025em] whitespace-nowrap"
                key={column.id}
                scope="col"
              >
                {column.label ?? column.id}
                {column.unit ? (
                  <>
                    {" "}
                    <span className="font-medium text-text-dim">({column.unit})</span>
                  </>
                ) : null}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr className="odd:bg-[rgb(255_255_255_/_1.4%)]" key={rowIndex}>
              {row.cells.map((cell, cellIndex) => (
                <td
                  className="border-b border-line px-2.5 py-2 font-mono text-text-soft last:border-r-0"
                  key={`${rowIndex}:${preview.columns[cellIndex]?.id ?? cellIndex}`}
                  title={typeof cell === "number" ? exactNumber(cell) : undefined}
                >
                  {formatCell(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 ? <p className="m-0 p-3 text-[0.61rem] text-text-dim">No rows</p> : null}
      {content.truncated ? (
        <p className="m-0 p-3 text-[0.58rem] text-text-dim">
          Showing {rows.length} of {content.total_rows} rows
        </p>
      ) : null}
    </div>
  );
}

type FigureContent = Extract<AnalysisOutput, { kind: "figure" }>["content"];

function AnalysisFigureView({ content, title }: { content: FigureContent; title: string }) {
  const first = content.layers[0]!.preview;
  const series = content.layers.flatMap((layer) => layer.preview.series);
  const pointCount = series.reduce((total, item) => total + item.x.length, 0);
  const xLabel = analysisAxisLabel(first.x_axis);
  const yLabel = analysisAxisLabel(first.y_axis);
  const option = useMemo(() => analysisFigureOption(content), [content]);
  return (
    <figure className="m-0 p-[9px]" data-testid="analysis-figure">
      <figcaption className="mb-1.5 text-xs text-text-dim">
        {xLabel} → {yLabel}
      </figcaption>
      <EChart
        ariaLabel={`${title}: ${yLabel} by ${xLabel}`}
        height={270}
        option={option}
        pointCount={pointCount}
        seriesLabels={series.map((item) => item.label ?? item.id)}
        seriesCount={series.length}
      />
      <p className="text-xs text-text-dim">
        Showing {pointCount} of {content.total_points} points across {content.layers.length} layers
      </p>
      <ul className="text-xs text-text-dim">
        {content.layers.map((layer) => (
          <li key={layer.id}>
            <strong>{layer.id}</strong> · {layer.preview.kind} ·{" "}
            {layer.preview.series.reduce((n, item) => n + item.x.length, 0)}/{layer.total_points}{" "}
            points
            {layer.projection.uncertainty && (
              <p>
                {layer.projection.uncertainty.style === "band" ? "Band" : "Error bars"}:{" "}
                {layer.projection.uncertainty.meaning} (absolute lower/upper bounds)
              </p>
            )}
            <details>
              <summary>Retained source and projection</summary>
              {layer.source.kind === "dataset" ? (
                <p>
                  Dataset output in this publication: <code>{layer.source.output_id}</code>
                </p>
              ) : (
                <>
                  <p>
                    Analysis <code>{layer.source.source.analysis_record_id}</code> · dataset output{" "}
                    <code>{layer.source.source.output_id}</code>
                  </p>
                  <code>{layer.source.dataset.content_hash}</code>
                </>
              )}
              <pre className="overflow-auto">
                {JSON.stringify({ source: layer.source, projection: layer.projection }, null, 2)}
              </pre>
            </details>
          </li>
        ))}
      </ul>
    </figure>
  );
}

function formatCell(value: boolean | number | string | null): string {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "True" : "False";
  return typeof value === "number" ? exactNumber(value) : value;
}

function exactNumber(value: number): string {
  return Object.is(value, -0) ? "-0" : String(value);
}

export function AnalysisMetadataView({ metadata }: { metadata: Record<string, unknown> }) {
  if (Object.keys(metadata).length === 0) return null;
  return (
    <details className="border-t border-line px-[9px] py-[7px] text-[0.58rem] text-text-dim">
      <summary className="cursor-pointer font-bold">Metadata</summary>
      <pre className="mt-1.5 mb-0 max-h-36 overflow-auto whitespace-pre-wrap text-[0.58rem] text-text-soft">
        {JSON.stringify(metadata, null, 2)}
      </pre>
    </details>
  );
}

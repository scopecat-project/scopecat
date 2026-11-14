import { useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { navigate, useLocationUrl } from "../../lib/navigation";
import { Button, Field, Input, Notice } from "../../ui/Controls";
import { detailCard, pageContent, pageTitle, secondaryButton } from "../../ui/styles";
import { SavedCurve } from "../instruments/DirectMeasurements";
import {
  listDirectMeasurements,
  type SavedMeasurement,
} from "../instruments/direct-measurement-api";

export function directMeasurementTitle(record: SavedMeasurement): string {
  return (
    record.selection.title?.trim() ||
    record.selection.line_label?.trim() ||
    `${record.description.label || record.selection.instrument_id} · ${new Date(record.collected_at).toLocaleString()}`
  );
}

/** A retained result is independently addressable; it never needs a live device session. */
export function DirectMeasurementHistory({
  daemonUnavailable,
  projectId,
}: {
  daemonUnavailable: boolean;
  projectId: string;
}) {
  const url = useLocationUrl();
  const recordId = url.searchParams.get("measurement") || undefined;
  const [search, setSearch] = useState("");
  const records = useInfiniteQuery({
    queryKey: ["direct-measurements", "history", projectId],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => listDirectMeasurements(pageParam),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: !daemonUnavailable,
  });
  const selected = useQuery({
    queryKey: ["direct-measurement-detail", recordId],
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/direct-measurements/{record_id}", {
          params: { path: { record_id: recordId! } },
        }),
      ),
    enabled: !!recordId && !daemonUnavailable,
  });
  const loaded = records.data?.pages.flatMap((page) => page.items) ?? [];
  const term = search.trim().toLocaleLowerCase();
  const visible = loaded.filter((record) =>
    [
      directMeasurementTitle(record),
      record.selection.line_label,
      record.selection.note,
      record.description.label,
      record.selection.instrument_id,
      record.id,
    ]
      .join(" ")
      .toLocaleLowerCase()
      .includes(term),
  );
  return (
    <section className={`${pageContent} min-w-0`} aria-label="直接测量数据">
      <h2 className={pageTitle}>直接测量</h2>
      {daemonUnavailable && <Notice role="status">应用服务离线，暂时无法读取数据。</Notice>}
      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(16rem,1fr)_minmax(0,3fr)]">
        <div className={`${detailCard} min-w-0 self-start`}>
          <Field>
            搜索已加载记录
            <Input value={search} onChange={(event) => setSearch(event.target.value)} />
          </Field>
          {records.isPending && !daemonUnavailable && <p role="status">正在读取记录…</p>}
          {records.error && <Notice role="alert">读取记录失败：{records.error.message}</Notice>}
          {records.data && visible.length === 0 && (
            <p className="text-sm text-text-dim">
              {term ? "已加载记录中没有匹配项。" : "还没有保存的直接测量。"}
            </p>
          )}
          <ul className="mt-3 grid min-w-0 gap-2">
            {visible.map((record) => (
              <li key={record.id} className="min-w-0">
                <button
                  type="button"
                  className={`${secondaryButton} grid w-full min-w-0 justify-start gap-1 text-left whitespace-normal [overflow-wrap:anywhere] aria-pressed:border-accent aria-pressed:bg-accent/10 aria-pressed:ring-1 aria-pressed:ring-accent`}
                  aria-pressed={recordId === record.id}
                  onClick={() => {
                    const target = new URL(window.location.href);
                    target.searchParams.set("data", "direct-measurements");
                    target.searchParams.set("measurement", record.id);
                    target.hash = "history";
                    navigate(target);
                  }}
                >
                  <span>{directMeasurementTitle(record)}</span>
                  <span className="text-xs text-text-dim">
                    {record.description.label || record.selection.instrument_id} ·{" "}
                    {new Date(record.collected_at).toLocaleString()}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {records.hasNextPage && (
            <Button
              className="mt-3"
              disabled={daemonUnavailable || records.isFetchingNextPage}
              onClick={() => void records.fetchNextPage()}
            >
              {records.isFetchingNextPage ? "正在加载…" : "加载更早记录"}
            </Button>
          )}
        </div>
        <div className={`${detailCard} min-w-0`}>
          {!recordId && <p className="text-sm text-text-dim">选择一条记录查看结果。</p>}
          {recordId && selected.isPending && !daemonUnavailable && (
            <p role="status">正在打开测量…</p>
          )}
          {selected.error && <Notice role="alert">打开测量失败：{selected.error.message}</Notice>}
          {selected.data && !daemonUnavailable && (
            <SavedCurve key={selected.data.id} record={selected.data} />
          )}
        </div>
      </div>
    </section>
  );
}

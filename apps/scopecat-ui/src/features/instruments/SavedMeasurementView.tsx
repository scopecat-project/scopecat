import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { useDesktopAvailable } from "../application/DesktopSession";
import { SavedCurve } from "./DirectMeasurements";
import { secondaryButton } from "../../ui/styles";

export function SavedMeasurementLink({ recordId }: { recordId: string }) {
  const available = useDesktopAvailable();
  // A native control window must not fall back to the system browser while its bridge loads.
  const desktop = available || new URL(window.location.href).searchParams.get("desktop") === "1";
  const [error, setError] = useState<string>();
  const [opening, setOpening] = useState(false);
  const pending = useRef(false);
  const open = async () => {
    if (pending.current) return;
    setError(undefined);
    const api = window.pywebview?.api;
    if (!api?.open_saved_measurement_window) {
      setError("结果窗口尚不可用，请等待桌面连接就绪；若仍不可用，请更新桌面宿主。");
      return;
    }
    pending.current = true;
    setOpening(true);
    try {
      await api.open_saved_measurement_window(recordId);
    } catch (reason) {
      setError(`打开已保存结果失败：${reason instanceof Error ? reason.message : String(reason)}`);
    } finally {
      pending.current = false;
      setOpening(false);
    }
  };
  return (
    <>
      {desktop ? (
        <button className={secondaryButton} disabled={opening} onClick={() => void open()}>
          查看已保存结果
        </button>
      ) : (
        <a
          className={secondaryButton}
          href={`/?saved-measurement=${encodeURIComponent(recordId)}`}
          target="_blank"
          rel="noreferrer"
        >
          查看已保存结果
        </a>
      )}
      {error && <p role="alert">{error}</p>}
    </>
  );
}

/** This route reads a durable record, never a live session or the history index. */
export function SavedMeasurementView({ recordId }: { recordId: string }) {
  const desktop = useDesktopAvailable();
  const detail = useQuery({
    queryKey: ["direct-measurement-detail", recordId],
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/direct-measurements/{record_id}", {
          params: { path: { record_id: recordId } },
          signal,
        }),
      ),
    enabled: !!recordId,
  });
  const record = detail.data;
  const title = record?.selection.title || record?.selection.line_label || "未命名测量";
  useEffect(() => {
    if (!record) return;
    document.title = `${title} · 已保存结果`;
    if (desktop) void window.pywebview?.api.set_window_title(document.title).catch(() => {});
  }, [record, title, desktop]);
  return (
    <section className="min-w-0">
      <h1 className="mb-3 text-lg font-semibold">{record ? title : "已保存结果"}</h1>
      {!recordId && <p role="alert">缺少已保存结果 ID。</p>}
      {recordId && detail.isPending && <p role="status">正在读取已保存结果…</p>}
      {detail.error && <p role="alert">无法读取已保存结果：{detail.error.message}</p>}
      {record && <SavedCurve key={record.id} record={record} readOnly />}
    </section>
  );
}

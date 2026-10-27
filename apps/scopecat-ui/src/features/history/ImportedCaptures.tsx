import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { detailCard, secondaryButton } from "../../ui/styles";
import { CaptureDetail } from "./CaptureDetail";
import { requestOpenFile } from "../application/DesktopFiles";
import { useLocationUrl } from "../../lib/navigation";
import { selectCapture } from "./capture-location";
import { useDesktopAvailable } from "../application/DesktopSession";
import { ClearData } from "./DataCleanup";

export function ImportedCaptures({
  unavailable,
  projectId,
}: {
  unavailable: boolean;
  projectId: string;
}) {
  const selectedCapture = useLocationUrl().searchParams.get("capture");
  const desktop = useDesktopAvailable();
  const captures = useInfiniteQuery({
    queryKey: ["data", "captures", projectId],
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      apiData(
        apiClient.GET("/api/v1/data/captures", {
          params: { query: { offset: pageParam, limit: 100 } },
          signal,
        }),
      ),
    getNextPageParam: (page, _pages, offset) =>
      page.length === 100 ? offset + page.length : undefined,
    enabled: !unavailable,
  });
  const save = useMutation({
    mutationFn: (hash: string) => window.pywebview!.api.save_capture(hash),
  });
  const pending = save.isPending;
  const error = save.error ?? captures.error;
  return (
    <section className={detailCard} aria-label="导入的结果">
      <div className="flex items-center gap-3">
        <h3 className="font-semibold">导入的结果</h3>
        {desktop && (
          <details>
            <summary className="cursor-pointer text-sm">导入数据</summary>
            <button
              className={secondaryButton}
              disabled={unavailable || pending}
              onClick={() => {
                save.reset();
                requestOpenFile();
              }}
            >
              打开 Scopecat 文件…
            </button>
          </details>
        )}
      </div>
      {pending && <p role="status">正在保存文件…</p>}
      {save.data && !pending && <p role="status">已保存到 {save.data}</p>}
      {error && <p role="alert">{error.message}</p>}
      {captures.isLoading && <p>正在加载导入数据…</p>}
      {captures.data?.pages[0]?.length === 0 && <p>暂无导入数据。</p>}
      <ul className="space-y-3">
        {captures.data?.pages.flat().map((capture) => (
          <li key={capture.content_hash} className="min-w-0 break-words border-b border-line py-3">
            <p>入口运行：{capture.roots.join(", ")}</p>
            <p className="text-sm text-text-dim">来源：{capture.source_project_id}</p>
            <button
              className={secondaryButton}
              disabled={unavailable}
              onClick={() => selectCapture(capture.content_hash)}
            >
              查看数据
            </button>
            {desktop && (
              <button
                className={secondaryButton}
                disabled={unavailable || pending}
                onClick={() => {
                  save.mutate(capture.content_hash);
                }}
              >
                另存副本…
              </button>
            )}
            <details className="mt-2">
              <summary className="cursor-pointer text-sm">管理导入的结果</summary>
              <ClearData
                captures={[capture.content_hash]}
                onCleared={() => {
                  if (selectedCapture === capture.content_hash) selectCapture(undefined);
                }}
              />
            </details>
          </li>
        ))}
      </ul>
      {selectedCapture && <CaptureDetail key={selectedCapture} contentHash={selectedCapture} />}
      {captures.hasNextPage && (
        <button
          className={secondaryButton}
          disabled={captures.isFetchingNextPage}
          onClick={() => void captures.fetchNextPage()}
        >
          加载更多导入数据
        </button>
      )}
    </section>
  );
}

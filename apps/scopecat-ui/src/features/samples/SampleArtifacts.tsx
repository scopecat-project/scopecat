import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";

export function SampleArtifacts({ sampleId, revision }: { sampleId: string; revision: number }) {
  const query = useQuery({
    queryKey: ["sample-revisions", sampleId, revision, "artifacts"],
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/samples/{sample_id}/revisions/{revision}/artifacts", {
          params: { path: { sample_id: sampleId, revision } },
          signal,
        }),
      ),
  });
  return (
    <div className="mt-3 grid gap-2 border-t border-line pt-3" aria-label="样品附件">
      {query.isPending && <p role="status">正在检查附件可用性…</p>}
      {query.error && (
        <p role="alert">
          无法检查附件： {query.error.message}。重新连接后请刷新；维护者可核对此样品版本保存的引用。
        </p>
      )}
      {query.data?.items.map((item) => (
        <div
          key={item.artifact.id}
          className="rounded-md border border-line px-2.5 py-2 text-xs space-y-1"
        >
          <div className="flex items-center justify-between gap-2">
            <strong>{item.artifact.title}</strong>
            <span className="text-text-dim">{item.artifact.media_type ?? "reference"}</span>
          </div>
          <p className="text-text-dim">{item.reason}</p>
          {item.status !== "unavailable" && item.url ? (
            <a href={item.url} target="_blank" rel="noopener noreferrer" className="underline">
              {item.status === "external"
                ? "打开外部网站"
                : item.artifact.media_type === "application/pdf"
                  ? "下载文档"
                  : "打开附件"}
            </a>
          ) : (
            <>
              <p className="font-semibold">附件不可用</p>
              <p>{item.repair}</p>
              <code className="break-all">{item.artifact.uri}</code>
            </>
          )}
        </div>
      ))}
    </div>
  );
}

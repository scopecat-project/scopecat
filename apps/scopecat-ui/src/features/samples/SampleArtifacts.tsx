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
    <div className="mt-3 grid gap-2 border-t border-line pt-3" aria-label="Sample attachments">
      {query.isPending && <p role="status">Checking attachment delivery…</p>}
      {query.error && (
        <p role="alert">
          Attachments could not be checked: {query.error.message}. Refresh after reconnecting; a
          maintainer can verify this sample revision's stored references.
        </p>
      )}
      {query.data?.items.map((item) => (
        <div
          key={item.artifact.id}
          className="rounded-md border border-line px-2.5 py-2 text-[0.68rem] space-y-1"
        >
          <div className="flex items-center justify-between gap-2">
            <strong>{item.artifact.title}</strong>
            <span className="text-text-dim">{item.artifact.media_type ?? "reference"}</span>
          </div>
          <p className="text-text-dim">{item.reason}</p>
          {item.status !== "unavailable" && item.url ? (
            <a href={item.url} target="_blank" rel="noopener noreferrer" className="underline">
              {item.status === "external"
                ? "Open external website"
                : item.artifact.media_type === "application/pdf"
                  ? "Download document"
                  : "Open attachment"}
            </a>
          ) : (
            <>
              <p className="font-semibold">Attachment unavailable</p>
              <p>{item.repair}</p>
              <code className="break-all">{item.artifact.uri}</code>
            </>
          )}
        </div>
      ))}
    </div>
  );
}

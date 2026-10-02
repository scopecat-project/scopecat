import { useEffect, useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { detailCard, secondaryButton } from "../../ui/styles";
import { CaptureDetail } from "./CaptureDetail";

export function ImportedCaptures({ unavailable }: { unavailable: boolean }) {
  const cache = useQueryClient();
  const [selectedCapture, setSelectedCapture] = useState<string>();
  const [desktop, setDesktop] = useState(!!window.pywebview);
  useEffect(() => {
    const ready = () => setDesktop(true);
    window.addEventListener("pywebviewready", ready);
    return () => window.removeEventListener("pywebviewready", ready);
  }, []);
  const captures = useInfiniteQuery({
    queryKey: ["data", "captures"],
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
  const open = useMutation({
    mutationFn: () => window.pywebview!.api.open_capture(),
    onSuccess: (receipt) => {
      if (receipt) {
        setSelectedCapture(receipt.capture.content_hash);
        void cache.invalidateQueries({ queryKey: ["data", "captures"] });
      }
    },
  });
  const save = useMutation({
    mutationFn: (hash: string) => window.pywebview!.api.save_capture(hash),
  });
  const pending = open.isPending || save.isPending;
  const error = open.error ?? save.error ?? captures.error;
  return (
    <section className={detailCard} aria-label="Imported data">
      <div className="flex items-center gap-3">
        <h3 className="font-semibold">Imported data</h3>
        {desktop && (
          <button
            className={secondaryButton}
            disabled={unavailable || pending}
            onClick={() => {
              save.reset();
              open.mutate();
            }}
          >
            Open Scopecat file…
          </button>
        )}
      </div>
      {pending && (
        <p role="status">
          {open.isPending ? "Opening and checking the selected file…" : "Saving file…"}
        </p>
      )}
      {open.data && !pending && (
        <p role="status">
          {open.data.created ? "File imported." : "This data is already available."}
        </p>
      )}
      {save.data && !pending && <p role="status">Saved to {save.data}</p>}
      {error && <p role="alert">{error.message}</p>}
      {captures.isLoading && <p>Loading imported data…</p>}
      {captures.data?.pages[0]?.length === 0 && <p>No imported data yet.</p>}
      <ul className="space-y-3">
        {captures.data?.pages.flat().map((capture) => (
          <li key={capture.content_hash} className="border-b border-line py-3">
            <p>Runs: {capture.roots.join(", ")}</p>
            <p className="text-sm text-text-dim">Source: {capture.source_project_id}</p>
            <button
              className={secondaryButton}
              disabled={unavailable}
              onClick={() => setSelectedCapture(capture.content_hash)}
            >
              View data
            </button>
            {desktop && (
              <button
                className={secondaryButton}
                disabled={unavailable || pending}
                onClick={() => {
                  open.reset();
                  save.mutate(capture.content_hash);
                }}
              >
                Save a copy…
              </button>
            )}
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
          More imported data
        </button>
      )}
    </section>
  );
}

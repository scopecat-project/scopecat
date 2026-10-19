import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Notice } from "../../ui/Controls";
import { errorMessage } from "../../lib/presentation";
import type { LessonTopic, NotebookJourney } from "../application/DesktopSession";

export function NotebookCleanup({
  topic,
  journey,
  disabled,
  onBusyChange,
}: {
  topic: LessonTopic;
  journey: NotebookJourney;
  disabled: boolean;
  onBusyChange: (busy: boolean) => void;
}) {
  const api = window.pywebview?.api;
  const cache = useQueryClient();
  const [discard, setDiscard] = useState(journey.cleanup_files === "discard");
  const preview = useMutation({ mutationFn: () => api!.preview_notebook_cleanup!(topic) });
  const fixedFiles = preview.data?.files ?? journey.cleanup_files;
  const deleting = fixedFiles ? fixedFiles === "discard" : discard;
  const clear = useMutation({
    mutationFn: () =>
      api!.clear_notebook_course!(
        topic,
        JSON.stringify({
          preview: preview.data!.preview,
          files: fixedFiles ?? (discard ? "discard" : "preserve"),
        }),
      ),
    onMutate: () => onBusyChange(true),
    onSettled: async () => {
      onBusyChange(false);
      await cache.invalidateQueries({ queryKey: ["notebook-journey", topic] });
    },
  });
  if (journey.cleared)
    return <Notice>Course data cleared. Prepare again to start a fresh course folder.</Notice>;
  if (!journey.scope_id)
    return (
      <Notice>
        Older course: no reliable ownership record. Review its records individually in Data; cleanup
        will not infer ownership from names.
      </Notice>
    );
  if (!api?.preview_notebook_cleanup || !api.clear_notebook_course) return null;
  const result = preview.data;
  return (
    <details className="grid gap-2">
      <summary>Clear this course</summary>
      <p>
        Preview all owned data first. Finish or cancel unfinished tasks using their normal controls.
        Edited code is kept unless you explicitly choose to delete it.
      </p>
      <Button
        disabled={disabled || preview.isPending || clear.isPending}
        onClick={() => preview.mutate()}
      >
        Preview course cleanup
      </Button>
      {(preview.error || clear.error) && (
        <Notice role="alert">{errorMessage(preview.error ?? clear.error)}</Notice>
      )}
      {result && (
        <div className="grid gap-3 mt-3">
          <p>{result.preview.record_count} owned records selected.</p>
          <details>
            <summary>Review every selected record</summary>
            {Object.entries(result.preview.selection)
              .filter(([, ids]) => ids.length > 0)
              .map(([kind, ids]) => (
                <div key={kind}>
                  <strong>{kind}</strong>
                  <ul>
                    {ids.map((id) => (
                      <li key={id} className="break-all">
                        {id}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
          </details>
          {result.preview.blockers.map((blocker) => (
            <Notice key={blocker.owner} role="alert">
              {blocker.owner}: {blocker.reason}
            </Notice>
          ))}
          <label className="flex gap-2 items-start">
            <input
              type="checkbox"
              checked={deleting}
              disabled={!!fixedFiles || !!result.files_error || clear.isPending}
              onChange={(event) => setDiscard(event.target.checked)}
            />
            Also delete this course’s edited code and local Python folder: {journey.directory}
          </label>
          {result.files_error && <Notice>{result.files_error} Code can still be kept.</Notice>}
          <Button
            variant="danger"
            disabled={
              disabled ||
              clear.isPending ||
              result.preview.blockers.length > 0 ||
              (deleting && !!result.files_error)
            }
            onClick={() => clear.mutate()}
          >
            {clear.isPending ? "Clearing…" : "Confirm course cleanup"}
          </Button>
        </div>
      )}
    </details>
  );
}

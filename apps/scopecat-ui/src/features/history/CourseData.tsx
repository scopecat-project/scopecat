import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { Button, Notice } from "../../ui/Controls";
import { navigate, navigateLink } from "../../lib/navigation";

export function CourseData({ scopeId, unavailable }: { scopeId: string; unavailable: boolean }) {
  const scope = useQuery({
    queryKey: ["course-data", scopeId],
    queryFn: async () => {
      const [course, preview] = await Promise.all([
        apiData(
          apiClient.GET("/api/v1/practice/{scope_id}", { params: { path: { scope_id: scopeId } } }),
        ),
        apiData(
          apiClient.GET("/api/v1/notebook-practice/{scope_id}/preview", {
            params: { path: { scope_id: scopeId } },
          }),
        ),
      ]);
      return { course, preview };
    },
    enabled: !unavailable,
  });
  return (
    <section aria-label="Course data" className="rounded-lg border border-line p-4 grid gap-2">
      <h3 className="font-semibold">Course: {scope.data?.course.lesson ?? scopeId}</h3>
      <p>
        Measurements below are filtered by recorded ownership. This includes no ordinary work or
        other courses.
      </p>
      {scope.error && <Notice role="alert">{scope.error.message}</Notice>}
      {scope.data?.course.state === "cleared" ? (
        <Notice>This course has been cleared.</Notice>
      ) : (
        scope.data && (
          <>
            <p>
              {scope.data.preview.record_count} owned records, including{" "}
              {scope.data.preview.selection.procedures.length} tasks and{" "}
              {scope.data.preview.selection.analyses.length} independent analyses.
            </p>
            {scope.data.preview.selection.procedures.map((id) => (
              <a
                key={id}
                className="underline break-all"
                href={`?procedure=${encodeURIComponent(id)}#launch`}
                onClick={navigateLink}
              >
                Task {id}
              </a>
            ))}
            <details>
              <summary>Owned configuration and analysis records</summary>
              {Object.entries(scope.data.preview.selection)
                .filter(([kind, ids]) => !["runs", "procedures"].includes(kind) && ids.length)
                .map(([kind, ids]) => (
                  <div key={kind}>
                    <strong>{kind}</strong>
                    <ul>
                      {ids.map((id) => (
                        <li key={id} className="break-all">
                          {kind === "analyses" ? (
                            <a
                              className="underline"
                              href={`?analysis=${encodeURIComponent(id)}#analyses`}
                              onClick={navigateLink}
                            >
                              {id}
                            </a>
                          ) : (
                            id
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
            </details>
          </>
        )
      )}
      <Button
        onClick={() => {
          const url = new URL(window.location.href);
          url.searchParams.delete("course");
          navigate(url);
        }}
      >
        Show all Data
      </Button>
    </section>
  );
}

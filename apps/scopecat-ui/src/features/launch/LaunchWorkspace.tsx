import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { LaunchForm } from "./LaunchForm";
import { ProcedureHistory } from "./ProcedureHistory";
import { ProcedureProgress } from "./ProcedureProgress";

export function LaunchWorkspace() {
  const catalog = useQuery({
    queryKey: ["experiment-launcher"],
    queryFn: async () => {
      const result = await apiData(apiClient.GET("/api/v1/experiment-launcher"));
      return result.entries;
    },
  });
  const [procedureId, setProcedureId] = useState(
    () => new URLSearchParams(window.location.search).get("procedure") ?? "",
  );
  function admitted(id: string) {
    setProcedureId(id);
    const url = new URL(window.location.href);
    url.searchParams.set("procedure", id);
    window.history.replaceState(null, "", url);
  }
  const [selected, setSelected] = useState("");
  const entry = catalog.data?.find((item) => item.id === selected) ?? catalog.data?.[0];
  return (
    <section className="p-6 space-y-4">
      <h2 className="text-lg font-semibold">Experiments</h2>
      <p>Select a maintained experiment and preview its configured parameters.</p>
      {catalog.isPending && <p role="status">Loading experiments…</p>}
      {catalog.error && <p role="alert">{catalog.error.message}</p>}
      {catalog.data?.length === 0 && <p>This project has no registered experiments.</p>}
      {entry && (
        <>
          <label className="block">
            Experiment{" "}
            <select
              aria-label="Experiment"
              value={entry.id}
              onChange={(event) => setSelected(event.target.value)}
              className="border rounded p-2 ml-2"
            >
              {catalog.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </select>
          </label>
          <LaunchForm key={`${entry.id}:${entry.version}`} entry={entry} onAdmitted={admitted} />
        </>
      )}
      <ProcedureHistory selectedId={procedureId} onSelect={admitted} />
      {procedureId && <ProcedureProgress key={procedureId} procedureId={procedureId} />}
    </section>
  );
}

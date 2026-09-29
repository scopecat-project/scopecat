import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api-client";
import { errorMessage } from "../../lib/presentation";
import { getDriverSource, updateDriverSource, type DriverSourceUpdate } from "./device-api";
import { createInstrumentCommandId } from "./instrument-api";

export function DriverSourcePanel({
  daemonUnavailable,
  sessionBusy,
}: {
  daemonUnavailable: boolean;
  sessionBusy: boolean;
}) {
  const client = useQueryClient();
  const [directory, setDirectory] = useState<string>();
  const attempt = useRef<DriverSourceUpdate | undefined>(undefined);
  const source = useQuery({
    queryKey: ["driver-source"],
    queryFn: ({ signal }) => getDriverSource(signal),
    enabled: !daemonUnavailable,
  });
  const path = directory ?? source.data?.active?.request.source_root ?? "";
  const update = useMutation({
    retry: false,
    mutationFn: () => {
      attempt.current ??= {
        operation_id: createInstrumentCommandId("driver-source"),
        source_root: path.trim(),
        expected_previous: source.data?.active?.request.operation_id ?? null,
        actor: "local-operator",
      };
      return updateDriverSource(attempt.current);
    },
    onSuccess: () => {
      attempt.current = undefined;
    },
    onError: (error) => {
      // A rejected request can be resubmitted against the refreshed selection.
      // An uncertain transport result must reuse its original operation ID.
      if (error instanceof ApiError && error.status === 409) attempt.current = undefined;
    },
    onSettled: async () => {
      await Promise.all(
        ["driver-source", "devices", "instruments", "instrument-drivers", "device-drivers"].map(
          (key) => client.invalidateQueries({ queryKey: [key] }),
        ),
      );
    },
  });
  return (
    <form
      className="mt-4 grid gap-3 border-t border-line pt-3"
      aria-label="Update driver source"
      onSubmit={(event) => {
        event.preventDefault();
        update.mutate();
      }}
    >
      <p className="text-sm text-text-dim">
        Debug drivers from a source directory without reinstalling the application. Finish active
        experiments and release manual sessions first. Updating disconnects idle devices; it does
        not reconnect them.
      </p>
      {source.data && (
        <p className="text-sm">
          {source.data.active
            ? `Active driver source: ${source.data.active.request.source_root}`
            : "Using installed drivers."}
        </p>
      )}
      <label className="grid gap-1">
        Driver source directory
        <input
          value={path}
          disabled={update.isPending}
          aria-describedby="driver-source-help"
          onChange={(event) => {
            setDirectory(event.target.value);
            attempt.current = undefined;
            update.reset();
          }}
        />
      </label>
      <p id="driver-source-help" className="text-sm text-text-dim">
        Use an absolute path on the application computer, containing scopecat.toml. Experiment and
        compiler edits refresh separately when preparing experiments.
      </p>
      {sessionBusy && <p>Release the manual session before updating drivers.</p>}
      <button
        type="submit"
        disabled={
          daemonUnavailable ||
          sessionBusy ||
          !path.trim() ||
          !source.isSuccess ||
          source.isFetching ||
          update.isPending
        }
      >
        {update.isPending ? "Updating drivers…" : "Update from source"}
      </button>
      {(source.error || update.error) && (
        <p role="alert">{errorMessage(update.error ?? source.error)}</p>
      )}
      {update.isSuccess && (
        <p role="status">Driver source updated. Reconnect devices and prepare experiments again.</p>
      )}
    </form>
  );
}

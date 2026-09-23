import type { components } from "../../api-schema";
import { ApiError } from "../../api-client";
import { ConfigurationTemplatesPanel } from "./ConfigurationTemplatesPanel";
import { ExecutionScenario } from "../../ui/ExecutionScenario";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ConfigProfileSnapshot } from "../../api-contract";
import { errorMessage } from "../../lib/presentation";
import { secondaryButton } from "../../ui/styles";
import { createConfigOperationId, parseConfigProfileJson } from "./config-api";
import {
  activateSetup,
  getActiveSetup,
  getSetupRevisions,
  saveSetupFromConfig,
  type SetupActivateCommand,
} from "./setup-api";

export function SetupPanel({
  config,
  operator,
  onSelectConfiguration,
}: {
  config: ConfigProfileSnapshot | undefined;
  operator: string;
  onSelectConfiguration?: (choice: components["schemas"]["ConfigurationChoice-Input"]) => void;
}) {
  const cache = useQueryClient();
  const current = useQuery({
    queryKey: ["setup", "active"],
    queryFn: ({ signal }) => getActiveSetup(signal),
  });
  const revisions = useQuery({
    queryKey: ["setup", "revisions"],
    queryFn: ({ signal }) => getSetupRevisions(signal),
  });
  const [imported, setImported] = useState<ConfigProfileSnapshot>();
  const [importError, setImportError] = useState<string>();
  const sourceConfig = imported ?? config;
  const [name, setName] = useState("");
  const [selected, setSelected] = useState("");
  const [review, setReview] = useState<SetupActivateCommand>();
  const refresh = async () => {
    await Promise.all([
      cache.invalidateQueries({ queryKey: ["setup"] }),
      cache.invalidateQueries({ queryKey: ["instruments"] }),
    ]);
  };
  const save = useMutation({
    mutationFn: () => {
      if (!sourceConfig) throw new Error("Select a saved configuration first.");
      return saveSetupFromConfig(sourceConfig, name.trim(), operator.trim());
    },
    onSuccess: async (revision) => {
      setSelected(revision.id);
      setName("");
      setReview(undefined);
      await refresh();
    },
  });
  const activate = useMutation({
    mutationFn: activateSetup,
    onSuccess: async () => {
      setReview(undefined);
      await refresh();
    },
  });
  const candidate = revisions.data?.items.find((item) => item.id === selected);
  const noActiveSetup = current.error instanceof ApiError && current.error.status === 404;
  const canSelectSetup = Boolean(current.data) || noActiveSetup;
  const error =
    (noActiveSetup ? undefined : current.error) ?? revisions.error ?? save.error ?? activate.error;
  return (
    <section
      aria-label="Executable setup"
      className="grid gap-3 rounded-lg border border-line bg-panel p-3.5"
    >
      <h3>Executable setup</h3>
      <ConfigurationTemplatesPanel
        actor={operator}
        activeSetupHash={current.data?.revision.content_hash}
        onSelectConfiguration={onSelectConfiguration}
        onImported={async (result) => {
          setSelected(result.setup.id);
          setReview(undefined);
          await Promise.all([refresh(), cache.invalidateQueries({ queryKey: ["config"] })]);
        }}
      />
      <p>
        Current setup:{" "}
        <strong>
          {current.data?.revision.id ?? (noActiveSetup ? "Not selected" : "Loading…")}
        </strong>
        . Setup selects topology, routing, instrument identities and configured defaults. Parameter
        defaults and saved working points are selected separately.
      </p>
      {current.data && (
        <ExecutionScenario
          scenario={current.data.revision.setup.scenario}
          label="Current setup scenario"
        />
      )}
      <div className="flex flex-wrap items-center gap-2">
        <label>
          Setup revision name{" "}
          <input
            aria-label="Setup revision name"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <button
          className={secondaryButton}
          disabled={!sourceConfig || !name.trim() || !operator.trim() || save.isPending}
          onClick={() => save.mutate()}
        >
          {imported
            ? "Save setup from imported snapshot"
            : "Save setup from selected configuration"}
        </button>
      </div>
      <label>
        Read setup from snapshot file{" "}
        <input
          aria-label="Setup snapshot file"
          type="file"
          accept=".json,application/json"
          onChange={async (event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            try {
              setImported(parseConfigProfileJson(await file.text()));
              setImportError(undefined);
            } catch (importFailure) {
              setImportError(errorMessage(importFailure));
            }
          }}
        />
      </label>
      {imported && (
        <p>
          Setup source: imported snapshot {imported.id}.{" "}
          <button className={secondaryButton} onClick={() => setImported(undefined)}>
            Use selected configuration instead
          </button>
        </p>
      )}
      {importError && <p role="alert">{importError}</p>}
      <p>Saving preserves an immutable revision without changing the current setup.</p>
      <div className="flex flex-wrap items-center gap-2">
        <label>
          Saved setup{" "}
          <select
            aria-label="Saved setup"
            value={selected}
            onChange={(event) => {
              setSelected(event.target.value);
              setReview(undefined);
              activate.reset();
            }}
          >
            <option value="">Choose a setup</option>
            {revisions.data?.items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.id}
              </option>
            ))}
          </select>
        </label>
        <button
          className={secondaryButton}
          disabled={!candidate || !canSelectSetup || !operator.trim() || activate.isPending}
          onClick={() => {
            if (!candidate || !canSelectSetup) return;
            setReview({
              operation_id: createConfigOperationId("setup"),
              revision: { revision_id: candidate.id, content_hash: candidate.content_hash },
              expected_generation: current.data?.activation.generation ?? 0,
              actor: operator.trim(),
              note: "",
              changes: [],
            });
          }}
        >
          Review setup selection
        </button>
      </div>
      {candidate && (
        <ExecutionScenario scenario={candidate.setup.scenario} label="Selected revision scenario" />
      )}
      {review && candidate && (
        <div
          role="region"
          aria-label="Review setup selection"
          className="grid gap-2 border border-line p-3"
        >
          <p>
            Select <strong>{candidate.id}</strong>:{" "}
            {candidate.setup.instrument_registry.instruments.length} instruments,{" "}
            {candidate.setup.topology.entities?.length ?? 0} control entities.
          </p>
          <p>
            This changes the setup for the entire experiment service, including other pages and
            notebooks. Parameter defaults remain unchanged. Existing configurations retain their
            original setup. Rebind parameters explicitly before running them with a different setup.
            Ongoing device ownership can block selection.
          </p>
          <p>
            Removing or rekeying physical instruments requires explicit declarations through
            lab.setup.activate.
          </p>
          <button
            className={secondaryButton}
            disabled={activate.isPending}
            onClick={() => activate.mutate(review)}
          >
            Confirm setup selection
          </button>
          <button
            className={secondaryButton}
            disabled={activate.isPending}
            onClick={() => setReview(undefined)}
          >
            Cancel setup selection
          </button>
        </div>
      )}
      {error && <p role="alert">{errorMessage(error)}</p>}
    </section>
  );
}

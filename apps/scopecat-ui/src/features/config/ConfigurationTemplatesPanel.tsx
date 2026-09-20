import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { components } from "../../api-schema";
import { ExecutionScenario } from "../../ui/ExecutionScenario";
import { errorMessage } from "../../lib/presentation";
import { secondaryButton } from "../../ui/styles";
import { createConfigOperationId } from "./config-api";
import {
  getConfigurationTemplates,
  importConfigurationTemplate,
  type ConfigurationTemplateImportCommand,
  type ConfigurationTemplateImportResult,
} from "./setup-api";

export function ConfigurationTemplatesPanel({
  actor,
  activeSetupHash,
  onImported,
  onSelectConfiguration,
}: {
  actor: string;
  activeSetupHash?: string;
  onImported: (result: ConfigurationTemplateImportResult) => Promise<void>;
  onSelectConfiguration?: (ref: components["schemas"]["PlanConfigRef"]) => void;
}) {
  const templates = useQuery({
    queryKey: ["setup", "templates"],
    queryFn: ({ signal }) => getConfigurationTemplates(signal),
  });
  const [selected, setSelected] = useState("");
  const [command, setCommand] = useState<ConfigurationTemplateImportCommand>();
  const imported = useMutation({
    mutationFn: importConfigurationTemplate,
    onSuccess: (result) => onImported(result),
  });
  const candidate = templates.data?.items.find((item) => item.id === selected);
  const result = imported.data;
  const error = templates.error ?? imported.error;
  return (
    <section
      aria-label="Configuration templates"
      className="grid gap-2 rounded border border-line p-3"
    >
      <h4>Configuration templates</h4>
      <p>
        Import a complete setup and its independent parameters from the current adapter. Importing
        changes neither the service setup nor the lab parameter default.
      </p>
      <label>
        Available template{" "}
        <select
          aria-label="Available template"
          value={selected}
          disabled={imported.isPending}
          onChange={(event) => {
            setSelected(event.target.value);
            setCommand(undefined);
            imported.reset();
          }}
        >
          <option value="">Choose a template</option>
          {templates.data?.items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      {templates.isPending && <p>Loading templates…</p>}
      {templates.data?.items.length === 0 && (
        <p>This adapter declares no configuration templates.</p>
      )}
      {candidate && (
        <>
          <p>{candidate.description}</p>
          <ExecutionScenario
            scenario={candidate.config.system.scenario}
            label="Template scenario"
          />
          <button
            className={secondaryButton}
            disabled={!actor.trim() || imported.isPending || Boolean(result)}
            onClick={() => {
              const next = command ?? {
                template_id: candidate.id,
                content_hash: candidate.content_hash,
                entry_id: createConfigOperationId("template"),
                actor: actor.trim(),
                note: "",
              };
              setCommand(next);
              imported.mutate(next);
            }}
          >
            {imported.isPending
              ? "Importing template…"
              : command && imported.isError
                ? "Retry template import"
                : "Import configuration template"}
          </button>
        </>
      )}
      {result && (
        <div role="status">
          <p>
            Imported setup <code>{result.setup.id}</code> and configuration{" "}
            <code>{result.configuration.entry.id}</code>.
          </p>
          <p>
            Review and confirm the imported setup below. Setup selection affects this entire
            experiment service, including other pages and notebooks; the parameter default stays
            unchanged.
          </p>
          {onSelectConfiguration && (
            <button
              className={secondaryButton}
              disabled={activeSetupHash !== result.setup.content_hash}
              onClick={() =>
                onSelectConfiguration({
                  entry_id: result.configuration.entry.id,
                  content_hash: result.configuration.entry.content_hash,
                })
              }
            >
              Use imported configuration for next experiment
            </button>
          )}
        </div>
      )}
      {error && <p role="alert">{errorMessage(error)}</p>}
    </section>
  );
}

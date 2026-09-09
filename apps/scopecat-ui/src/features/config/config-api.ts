import { ApiError, apiClient, apiData } from "../../api-client";
import type {
  ConfigDraftCommand,
  ConfigDraftPreview,
  ConfigActivationPage,
  ConfigActivationRecord,
  ConfigProfileSnapshot,
  ConfigRegistryEntry,
  ConfigRegistryOverview,
  ConfigRegistryPage,
  ConfigPublishCommand,
  ConfigPublishReceipt,
} from "../../api-contract";
import type { components } from "../../api-schema";

export interface ConfigSnapshotSummary {
  id: string;
  primaryEntityId: string;
  parameterCount: number;
  instrumentCount: number;
}

export interface ConfigRegistryEntryDetail {
  entry: ConfigRegistryEntry;
  config: ConfigProfileSnapshot;
  summary: ConfigSnapshotSummary;
  latestActivation?: ConfigActivationRecord | null;
}

export async function getConfigRegistry(signal?: AbortSignal): Promise<ConfigRegistryOverview> {
  const [registry, activations] = await Promise.all([
    apiData(
      apiClient.GET("/api/v1/config-registry", {
        params: { query: { limit: 100 } },
        signal,
      }),
    ),
    apiData(
      apiClient.GET("/api/v1/config-registry/activations", {
        params: { query: { limit: 100 } },
        signal,
      }),
    ),
  ]);
  return {
    entries: registry.entries,
    activation: registry.activation,
    activation_history: activations.items,
    ...(registry.next_cursor === undefined || registry.next_cursor === null
      ? {}
      : { entries_next_cursor: registry.next_cursor }),
    ...(activations.next_cursor === undefined || activations.next_cursor === null
      ? {}
      : { activation_history_next_cursor: activations.next_cursor }),
  };
}

export async function getOlderConfigRegistryEntries(
  before: number,
  signal?: AbortSignal,
): Promise<ConfigRegistryPage> {
  return apiData(
    apiClient.GET("/api/v1/config-registry", {
      params: { query: { limit: 100, before } },
      signal,
    }),
  );
}

export async function getOlderConfigActivationHistory(
  before: number,
  signal?: AbortSignal,
): Promise<ConfigActivationPage> {
  return apiData(
    apiClient.GET("/api/v1/config-registry/activations", {
      params: { query: { limit: 100, before } },
      signal,
    }),
  );
}

export async function getConfigRegistryEntry(
  entryId: string,
  signal?: AbortSignal,
): Promise<ConfigRegistryEntryDetail> {
  const response = await apiData(
    apiClient.GET("/api/v1/config-registry/entries/{entry_id}", {
      params: { path: { entry_id: entryId } },
      signal,
    }),
  );
  const config = configSnapshot(response.config);
  return {
    entry: response.entry,
    config,
    summary: summarizeConfigSnapshot(config),
    latestActivation: response.latest_activation,
  };
}

export async function activateConfigEntry(
  command: components["schemas"]["ConfigEntryActivationCommand"],
): Promise<void> {
  await retryOneTransportFailure(() =>
    apiData(apiClient.POST("/api/v1/config-registry/activation-operations", { body: command })),
  );
}

export async function previewConfigDraft(command: ConfigDraftCommand): Promise<ConfigDraftPreview> {
  const response = await apiData(
    apiClient.POST("/api/v1/config-registry/drafts/preview", {
      body: command,
    }),
  );
  return response as ConfigDraftPreview;
}

export async function publishConfig(command: ConfigPublishCommand): Promise<ConfigPublishReceipt> {
  const response = await retryOneTransportFailure(() =>
    apiData(
      apiClient.POST("/api/v1/config-registry/publish-operations", {
        body: command,
      }),
    ),
  );
  return response as ConfigPublishReceipt;
}

export function createConfigOperationId(purpose: string): string {
  const random =
    typeof globalThis.crypto?.randomUUID === "function"
      ? globalThis.crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `ui-config-${purpose}-${random}`;
}

async function retryOneTransportFailure<Result>(send: () => Promise<Result>): Promise<Result> {
  try {
    return await send();
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== undefined) throw error;
    return send();
  }
}

function summarizeConfigSnapshot(config: ConfigProfileSnapshot): ConfigSnapshotSummary {
  return {
    id: config.id,
    primaryEntityId: config.system.primary_entity_id,
    parameterCount: config.parameter_snapshot.values?.length ?? 0,
    instrumentCount: config.system.instrument_registry.instruments.length,
  };
}

export function parseConfigProfileJson(textValue: string): ConfigProfileSnapshot {
  let parsed: unknown;
  try {
    parsed = JSON.parse(textValue);
  } catch {
    throw new Error("The selected file is not valid JSON.");
  }
  const profile = object(parsed, "selected config snapshot");
  const formatVersion = optionalText(profile.format_version);
  if (formatVersion !== "scopecat.config_snapshot.v10") {
    throw new Error(
      `Unsupported config snapshot format: ${formatVersion ?? "missing format_version"}.`,
    );
  }
  if (!optionalText(profile.id)) {
    throw new Error("The config snapshot is missing its id.");
  }
  if (!isObject(profile.system)) {
    throw new Error("The config snapshot is missing its system definition.");
  }
  if (!isObject(profile.parameter_snapshot)) {
    throw new Error("The config snapshot is missing its parameter values.");
  }
  const snapshot = { ...profile };
  delete snapshot.format_version;
  return snapshot as ConfigProfileSnapshot;
}

function configSnapshot(source: unknown): ConfigProfileSnapshot {
  // OpenAPI emits separate input/output aliases for the same JSON snapshot.
  return source as ConfigProfileSnapshot;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (!isObject(value)) throw new Error(`${label} must be an object.`);
  return value;
}

function optionalText(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

export type ConfigContextRef = components["schemas"]["ConfigContextRef"];
export type ConfigContextResolution = Awaited<ReturnType<typeof resolveConfigContext>>;
export type ConfigContextSaveCommand = components["schemas"]["ConfigContextSaveCommand"];

export async function saveConfigContext(command: ConfigContextSaveCommand) {
  return retryOneTransportFailure(() =>
    apiData(apiClient.POST("/api/v1/config-registry/contexts", { body: command })),
  );
}

export async function resolveConfigContext(
  context: ConfigContextRef,
  overrides: components["schemas"]["ConfigContextResolveCommand"]["overrides"] = [],
) {
  return apiData(
    apiClient.POST("/api/v1/config-registry/contexts/resolve", { body: { context, overrides } }),
  );
}

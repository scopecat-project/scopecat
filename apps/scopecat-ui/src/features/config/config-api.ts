import { apiClient, apiData } from "../../api-client";
import type { ConfigProfileSnapshot, ConfigRegistryEntry } from "../../api-contract";
import type { components } from "../../api-schema";

export interface ConfigSnapshotSummary {
  id: string;
  parameterCount: number;
  instrumentCount: number;
}

export interface ConfigRegistryEntryDetail {
  entry: ConfigRegistryEntry;
  config: ConfigProfileSnapshot;
  summary: ConfigSnapshotSummary;
  structureVersion?: string | null;
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
    structureVersion: response.structure_version,
  };
}

export function createConfigOperationId(purpose: string): string {
  const random =
    typeof globalThis.crypto?.randomUUID === "function"
      ? globalThis.crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `ui-config-${purpose}-${random}`;
}

function summarizeConfigSnapshot(config: ConfigProfileSnapshot): ConfigSnapshotSummary {
  return {
    id: config.id,
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
  if (formatVersion !== "scopecat.config_snapshot.v11") {
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

export async function resolveConfigContext(
  context: ConfigContextRef,
  overrides: components["schemas"]["ConfigContextResolveCommand"]["overrides"] = [],
) {
  return apiData(
    apiClient.POST("/api/v1/config-registry/contexts/resolve", { body: { context, overrides } }),
  );
}

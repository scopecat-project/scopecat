import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import type { ConfigProfileSnapshot } from "../../api-contract";

export type SetupRevision = components["schemas"]["SetupRevision"];
export type SavedSetupRevision = Awaited<ReturnType<typeof getSetupRevisions>>["items"][number];
export type ActiveSetupView = components["schemas"]["ActiveSetupView"];
export type SetupActivateCommand = components["schemas"]["SetupActivateCommand"];

export function getActiveSetup(signal?: AbortSignal) {
  return apiData(apiClient.GET("/api/v1/setup/active", { signal }));
}
export function getSetupRevisions(signal?: AbortSignal) {
  return apiData(apiClient.GET("/api/v1/setup/revisions", { signal }));
}
export function getSetupRevision(id: string, signal?: AbortSignal) {
  return apiData(
    apiClient.GET("/api/v1/setup/revisions/{revision_id}", {
      params: { path: { revision_id: id } },
      signal,
    }),
  );
}
export function importSetupRecipe(config: ConfigProfileSnapshot, name: string, actor: string) {
  const { topology, instrument_registry, routing, domain_target, scenario } = config.system;
  return apiData(
    apiClient.POST("/api/v1/setup/recipe-imports", {
      body: {
        revision_id: name,
        actor,
        note: "",
        setup: {
          topology,
          instrument_registry,
          routing: routing ?? { roles: [], routes: [] },
          domain_target: domain_target ?? null,
          scenario: scenario ?? null,
        },
      },
    }),
  );
}
export type SetupDefinition = Awaited<
  ReturnType<typeof getSetupDefinitions>
>["items"][number]["definition"];
export function getSetupDefinitions(signal?: AbortSignal) {
  return apiData(apiClient.GET("/api/v1/setup/definitions", { signal }));
}
export function resolveSetupDefinition(id: string) {
  return apiData(
    apiClient.POST("/api/v1/setup/resolutions/{definition_id}", {
      params: { path: { definition_id: id } },
    }),
  );
}
export function saveSetupDefinition(setup: SetupDefinition, name: string, actor: string) {
  return apiData(
    apiClient.POST("/api/v1/setup/revisions", {
      body: { setup, revision_id: name, actor, note: "" },
    }),
  );
}
export function activateSetup(command: SetupActivateCommand) {
  return apiData(apiClient.POST("/api/v1/setup/activation-operations", { body: command }));
}

export type ConfigurationTemplateImportCommand =
  components["schemas"]["ConfigurationTemplateImportCommand"];
export type { ConfigurationTemplateImportResult } from "../../api-contract";
export function getConfigurationTemplates(signal?: AbortSignal) {
  return apiData(apiClient.GET("/api/v1/setup/templates", { signal }));
}
export function importConfigurationTemplate(command: ConfigurationTemplateImportCommand) {
  return apiData(apiClient.POST("/api/v1/setup/template-imports", { body: command }));
}

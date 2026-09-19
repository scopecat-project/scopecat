import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import type { ConfigProfileSnapshot } from "../../api-contract";

export type SetupRevision = components["schemas"]["SetupRevision"];
export type ActiveSetupView = components["schemas"]["ActiveSetupView"];
export type SetupActivateCommand = components["schemas"]["SetupActivateCommand"];

export function getActiveSetup(signal?: AbortSignal) {
  return apiData(apiClient.GET("/api/v1/setup/active", { signal }));
}
export function getSetupRevisions(signal?: AbortSignal) {
  return apiData(apiClient.GET("/api/v1/setup/revisions", { signal }));
}
export function saveSetupFromConfig(config: ConfigProfileSnapshot, name: string, actor: string) {
  const { primary_entity_id, topology, instrument_registry, routing, domain_target } =
    config.system;
  return apiData(
    apiClient.POST("/api/v1/setup/revisions", {
      body: {
        revision_id: name,
        actor,
        note: "",
        setup: {
          primary_entity_id,
          topology,
          instrument_registry,
          routing: routing ?? { roles: [], routes: [] },
          domain_target: domain_target ?? null,
        },
      },
    }),
  );
}
export function activateSetup(command: SetupActivateCommand) {
  return apiData(apiClient.POST("/api/v1/setup/activation-operations", { body: command }));
}

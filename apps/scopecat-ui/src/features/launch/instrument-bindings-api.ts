import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";

export type BindingRequirements = components["schemas"]["BindingRequirements"];
export type BindingOptions = components["schemas"]["BindingOptions"];
export type BindingPlan = components["schemas"]["InstrumentBindingPlan"];
export type BindingChoice = components["schemas"]["InstrumentBindingChoice"];
export type BindingCheck = Awaited<ReturnType<typeof checkBindings>>;
export type BindingRequest = components["schemas"]["LaunchRequest-Input"];

export function inspectBindings(request: BindingRequest) {
  return apiData(
    apiClient.POST("/api/v1/experiment-launcher/bindings", {
      body: { ...request, action: "bindings", instrument_bindings: undefined },
    }),
  );
}
export function bindingOptions(requirements: BindingRequirements) {
  return apiData(apiClient.POST("/api/v1/setup/bindings/options", { body: requirements }));
}
export function checkBindings(plan: BindingPlan) {
  return apiData(apiClient.POST("/api/v1/setup/bindings/check", { body: plan }));
}

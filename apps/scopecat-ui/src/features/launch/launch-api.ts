import type { MethodResponse } from "openapi-fetch";
import type { apiClient } from "../../api-client";
import type { components } from "../../api-schema";

export type LaunchCatalogEntry = components["schemas"]["LaunchCatalogEntry"];
export type LaunchPreview = MethodResponse<
  typeof apiClient,
  "post",
  "/api/v1/experiment-launcher/preview"
>;

import createClient from "openapi-fetch";
import type { paths } from "./api-schema";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const apiClient = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "http://localhost",
  headers: { Accept: "application/json" },
  fetch: (request) => globalThis.fetch(request),
});

type ApiResponse<T> = Promise<
  | { data: T; error?: never; response: Response }
  | { data?: never; error: unknown; response: Response }
>;

export async function apiData<T>(pending: ApiResponse<T>): Promise<Exclude<T, undefined>> {
  let result: Awaited<ApiResponse<T>>;
  try {
    result = await pending;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    if (error instanceof SyntaxError) {
      throw new ApiError("The daemon returned an invalid JSON response.");
    }
    throw new ApiError("The local daemon did not respond.");
  }

  if (result.data !== undefined) {
    return result.data as Exclude<T, undefined>;
  }
  if (result.response.ok) {
    throw new ApiError("The daemon returned an invalid JSON response.");
  }

  const detail = isObject(result.error) ? result.error.detail : undefined;
  const message =
    typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail
            .filter(isObject)
            .map(
              (item) =>
                `${Array.isArray(item.loc) ? item.loc.join(".") + ": " : ""}${typeof item.msg === "string" ? item.msg : "Invalid value"}`,
            )
            .join("; ")
        : isObject(detail) && typeof detail.message === "string"
          ? detail.message
          : undefined;
  throw new ApiError(
    message || `The daemon returned ${result.response.status} ${result.response.statusText}.`,
    result.response.status,
    detail,
  );
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

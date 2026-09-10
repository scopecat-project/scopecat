import { afterEach, describe, expect, it, vi } from "vitest";
import { getHealth } from "./data/project-api";
import { ApiError } from "./api-client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("OpenAPI client boundary", () => {
  it("sends same-origin JSON requests with the default accept header", async () => {
    const fetchMock = vi.fn((_request: Request) =>
      Promise.resolve(
        jsonResponse({
          status: "ok",
          project_id: "local:test",
          project_name: "test",
          project_root: "/tmp/test",
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getHealth();

    const request = fetchMock.mock.calls[0]?.[0];
    expect(request).toBeInstanceOf(Request);
    expect(new URL(request!.url).pathname).toBe("/api/v1/health");
    expect(request!.headers.get("Accept")).toBe("application/json");
  });

  it("preserves the local daemon network error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("connection refused"))),
    );

    await expect(getHealth()).rejects.toEqual(new ApiError("The local daemon did not respond."));
  });

  it("distinguishes invalid success JSON from HTTP errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response("{", {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        )
        .mockResolvedValueOnce(new Response("", { status: 503, statusText: "Unavailable" })),
    );

    await expect(getHealth()).rejects.toThrow("The daemon returned an invalid JSON response.");
    await expect(getHealth()).rejects.toMatchObject({
      message: "The daemon returned 503 Unavailable.",
      status: 503,
    });
  });

  it("does not translate request cancellation into an API failure", async () => {
    const cancelled = new DOMException("cancelled", "AbortError");
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(cancelled)),
    );

    await expect(getHealth()).rejects.toBe(cancelled);
  });
});

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

it("keeps actionable validation locations and raw diagnostics", async () => {
  const detail = [{ loc: ["body", "drive", "q0", "frequency"], msg: "Use a frequency unit" }];
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ detail }), {
          status: 422,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );
  await expect(getHealth()).rejects.toMatchObject({
    status: 422,
    message: "body.drive.q0.frequency: Use a frequency unit",
    detail,
  });
});

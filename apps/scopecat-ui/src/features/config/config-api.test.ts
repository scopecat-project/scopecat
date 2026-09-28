import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConfigProfileSnapshot, ConfigRegistryEntry } from "../../api-contract";
import { requestPath } from "../../test/http";
import { getConfigRegistryEntry, parseConfigProfileJson } from "./config-api";

const HASH_A = `sha256:${"a".repeat(64)}`;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("config registry reads", () => {
  it("uses the entry snapshot itself for summary and raw display", async () => {
    const config = configProfile("profile-a");
    const fetchMock = vi.fn((_input: string | URL | Request) =>
      Promise.resolve(
        jsonResponse({
          entry: registryEntry("config-a", HASH_A),
          config,
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const detail = await getConfigRegistryEntry("config a");

    expect(requestPath(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/config-registry/entries/config%20a",
    );
    expect(detail.config).toEqual(config);
    expect(detail.config).not.toHaveProperty("raw");
    expect(detail.summary).toEqual({
      id: "profile-a",
      parameterCount: 1,
      instrumentCount: 1,
    });
  });
});

describe("config snapshot import boundary", () => {
  it("accepts only self-contained config snapshots", () => {
    const config = configProfile("profile-a");

    expect(
      parseConfigProfileJson(
        JSON.stringify({
          format_version: "scopecat.config_snapshot.v11",
          ...config,
        }),
      ),
    ).toEqual(config);
    expect(() => parseConfigProfileJson("{")).toThrow("not valid JSON");
    expect(() =>
      parseConfigProfileJson(
        JSON.stringify({
          ...config,
          format_version: "scopecat.config_snapshot.unsupported",
        }),
      ),
    ).toThrow("Unsupported config snapshot format");
  });
});

function registryEntry(id: string, contentHash: string): ConfigRegistryEntry {
  return {
    id,
    config_ref: `entries/${id}.json`,
    content_hash: contentHash,
    source: { kind: "direct_config_profile" },
    actor: "scopecat",
    note: "",
    recorded_at: id === "config-b" ? "2026-07-23T10:00:00Z" : "2026-07-22T10:00:00Z",
  };
}

function scalarValue(value: number) {
  return {
    id: "drive.frequency",
    shape: "scalar" as const,
    value: { value, unit: "GHz" },
  };
}

function configProfile(id: string): ConfigProfileSnapshot {
  return {
    id,
    system: {
      id: "system",
      topology: {
        entities: [{ id: "q0", kind: "logical_qubit", metadata: {} }],
      },
      instrument_registry: {
        instruments: [
          {
            id: "signal",
            exclusivity_key: "signal",
            driver_id: "virtual.signal_generator",
            connection: { kind: "virtual" },
            default_state: [],
            run_start: "preserve",
            success_action: "release",
            failure_action: "abort_and_release",
          },
        ],
      },
      routing: { roles: [], routes: [] },
      domain_target: null,
      parameter_catalog: {
        id: "parameters",
        definitions: [
          {
            id: "drive.frequency",
            value_type: {
              shape: "scalar",
              atom: { type: "quantity", finite: true, unit: "GHz" },
            },
            description: "Drive frequency",
          },
        ],
      },
    },
    parameter_snapshot: {
      id: "parameters",
      values: [scalarValue(5)],
    },
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

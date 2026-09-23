import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  ConfigDraftCommand,
  ConfigProfileSnapshot,
  ConfigRegistryEntry,
} from "../../api-contract";
import { requestJson, requestMethod, requestPath } from "../../test/http";
import {
  activateConfigEntry,
  getConfigRegistry,
  getConfigRegistryEntry,
  getOlderConfigActivationHistory,
  getOlderConfigRegistryEntries,
  parseConfigProfileJson,
  publishConfig,
  previewConfigDraft,
} from "./config-api";

const HASH_A = `sha256:${"a".repeat(64)}`;
const HASH_B = `sha256:${"b".repeat(64)}`;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("config registry reads", () => {
  it("keeps the daemon's newest-first paged registry projections", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) =>
      Promise.resolve(
        requestPath(input).includes("/activations?")
          ? jsonResponse({
              items: [activation(2, "config-b", HASH_B), activation(1, "config-a", HASH_A)],
            })
          : jsonResponse({
              entries: [registryEntry("config-b", HASH_B), registryEntry("config-a", HASH_A)],
              activation: activation(2, "config-b", HASH_B),
            }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const overview = await getConfigRegistry();

    expect(requestPath(fetchMock.mock.calls[0]?.[0])).toBe("/api/v1/config-registry?limit=100");
    expect(requestPath(fetchMock.mock.calls[1]?.[0])).toBe(
      "/api/v1/config-registry/activations?limit=100",
    );
    expect(overview.entries.map((entry) => entry.id)).toEqual(["config-b", "config-a"]);
    expect(overview.activation).toMatchObject({
      entry_id: "config-b",
      entry_content_hash: HASH_B,
      generation: 2,
    });
    expect(overview.activation_history.map((item) => item.generation)).toEqual([2, 1]);
  });

  it("returns null active state without inventing a second projection", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string | URL | Request) =>
        Promise.resolve(
          requestPath(input).includes("/activations?")
            ? jsonResponse({ items: [] })
            : jsonResponse({ entries: [], activation: null }),
        ),
      ),
    );

    await expect(getConfigRegistry()).resolves.toEqual({
      entries: [],
      activation: null,
      activation_history: [],
    });
  });

  it("requests older registry and activation pages by cursor", async () => {
    const fetchMock = vi.fn((_input: string | URL | Request) =>
      Promise.resolve(jsonResponse({ entries: [], items: [], next_cursor: null })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getOlderConfigRegistryEntries(41);
    await getOlderConfigActivationHistory(19);

    expect(fetchMock.mock.calls.map(([input]) => requestPath(input))).toEqual([
      "/api/v1/config-registry?limit=100&before=41",
      "/api/v1/config-registry/activations?limit=100&before=19",
    ]);
  });

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

describe("config registry commands", () => {
  it("sends an exact activation operation unchanged", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({})));
    vi.stubGlobal("fetch", fetchMock);
    const activationCommand = {
      operation_id: "ui-config-activate-1",
      entry_id: "config/b",
      actor: "Ada",
      note: "promote calibrated values",
      expected_generation: 2,
    };
    await activateConfigEntry(activationCommand);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    await expectRequest(
      fetchMock,
      0,
      "/api/v1/config-registry/activation-operations",
      activationCommand,
    );
  });
});

describe("typed config drafts", () => {
  const draft: ConfigDraftCommand = {
    base_entry_id: "config-a",
    base_content_hash: HASH_A,
    base_generation: 3,
    candidate_id: "config-a-edit",
    updates: [
      {
        kind: "replace_parameter",
        value: {
          id: "drive.frequency",
          shape: "scalar",
          value: { value: 5.2, unit: "GHz" },
        },
      },
    ],
  };

  it("passes preview responses and commands through the generated contract", async () => {
    const response = {
      valid: true,
      base_entry: registryEntry("config-a", HASH_A),
      base_generation: 3,
      base_content_hash: HASH_A,
      config: configProfile("config-a-edit"),
      result_content_hash: HASH_B,
      deltas: [
        {
          parameter_id: "drive.frequency",
          before: scalarValue(5),
          after: scalarValue(5.2),
        },
      ],
      problems: [],
    };
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(response)));
    vi.stubGlobal("fetch", fetchMock);

    await expect(previewConfigDraft(draft)).resolves.toEqual(response);
    await expectRequest(fetchMock, 0, "/api/v1/config-registry/drafts/preview", draft);
  });

  it("sends one publish command unchanged", async () => {
    const publishCommand = {
      operation_id: "ui-config-publish-1",
      source: {
        kind: "manual_parameter_updates" as const,
        draft,
        expected_result_content_hash: HASH_B,
      },
      entry_id: "config-a-edit",
      actor: "Ada",
      expected_generation: 3,
      note: "accepted edit",
    };
    const publishReceipt = {
      entry: registryEntry("config-a-edit", HASH_B),
      deltas: [
        {
          parameter_id: "drive.frequency",
          before: scalarValue(5),
          after: scalarValue(5.2),
        },
      ],
      activation: activation(4, "config-a-edit", HASH_B),
    };
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(publishReceipt));
    vi.stubGlobal("fetch", fetchMock);

    await expect(publishConfig(publishCommand)).resolves.toEqual(publishReceipt);
    await expectRequest(fetchMock, 0, "/api/v1/config-registry/publish-operations", publishCommand);
  });

  it("retries one transport failure with the exact publish command", async () => {
    const publishCommand = {
      operation_id: "ui-config-publish-stable",
      source: {
        kind: "manual_parameter_updates" as const,
        draft,
        expected_result_content_hash: HASH_B,
      },
      entry_id: "config-a-edit",
      actor: "Ada",
      expected_generation: 3,
      note: "accepted edit",
    };
    const receipt = {
      entry: registryEntry("config-a-edit", HASH_B),
      deltas: [],
      activation: activation(4, "config-a-edit", HASH_B),
      operation: {
        operation_id: publishCommand.operation_id,
        intent_hash: HASH_A,
        source_intent_hash: HASH_B,
        entry_id: publishCommand.entry_id,
        expected_generation: 3,
        actor: "Ada",
        note: "accepted edit",
        activation_generation: 4,
      },
    };
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce(jsonResponse(receipt));
    vi.stubGlobal("fetch", fetchMock);

    await expect(publishConfig(publishCommand)).resolves.toEqual(receipt);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const bodies = await Promise.all(
      fetchMock.mock.calls.map(([input, init]) => requestJson(input, init)),
    );
    expect(bodies).toEqual([publishCommand, publishCommand]);
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

function activation(generation: number, entryId: string, entryContentHash: string) {
  return {
    generation,
    action: "activation" as const,
    entry_id: entryId,
    entry_content_hash: entryContentHash,
    actor: "Ada",
    note: "",
    recorded_at: "2026-07-24T08:00:00Z",
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

async function expectRequest(
  fetchMock: ReturnType<typeof vi.fn>,
  index: number,
  path: string,
  body: object,
) {
  const call = fetchMock.mock.calls[index];
  expect(requestPath(call?.[0])).toBe(path);
  expect(requestMethod(call?.[0], call?.[1])).toBe("POST");
  await expect(requestJson(call?.[0], call?.[1])).resolves.toEqual(body);
}

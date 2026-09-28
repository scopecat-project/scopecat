import { ApiError } from "../../api-client";
import { apiClient, apiData } from "../../api-client";
import type {
  DriverCatalog,
  InstrumentAcquisition,
  InstrumentApplyReceipt,
  InstrumentCollectReceipt,
  InstrumentConfiguredDefaultsApplyReceipt,
  InstrumentDriverProbeCommand,
  InstrumentDriverProbeReceipt,
  InstrumentInvokeReceipt,
  InstrumentInvokeCommand,
  InstrumentList,
  InstrumentOperation,
  InstrumentSession,
  InstrumentSessionLease,
  InstrumentSpec,
  InstrumentState,
  InstrumentStateCache,
  InstrumentStateReadback,
  InstrumentStateTarget,
  InstrumentStateValue,
  InstrumentView,
} from "../../api-contract";
import type { SavedSetupRevision as SetupRevision } from "../config/setup-api";
import { decodeCollectReceipt, HARDWARE_RECEIPT_MEDIA_TYPE } from "./hardware-receipt-wire";

export type { InstrumentList } from "../../api-contract";

export interface StagedInstrumentMember {
  target: InstrumentStateTarget;
  value: InstrumentStateValue;
}

export interface InstrumentAcquisitionTarget {
  interfaceId: string;
  componentPath: string[];
  acquisition: InstrumentAcquisition;
}

export interface InstrumentOperationTarget {
  interfaceId: string;
  componentPath: string[];
  operation: InstrumentOperation;
}

export type InstrumentOperationArgument = NonNullable<InstrumentInvokeCommand["arguments"]>[number];

export async function getInstruments(
  setup: InstrumentList["setup"],
  signal?: AbortSignal,
): Promise<InstrumentList> {
  return apiData(
    apiClient.GET("/api/v1/instruments", {
      params: {
        query: { setup_revision_id: setup.revision_id, setup_content_hash: setup.content_hash },
      },
      signal,
    }),
  );
}

export async function getDriverCatalog(signal?: AbortSignal): Promise<DriverCatalog> {
  return apiData(apiClient.GET("/api/v1/instrument-drivers", { signal }));
}

export async function probeInstrumentDriver(
  command: InstrumentDriverProbeCommand,
): Promise<InstrumentDriverProbeReceipt> {
  return apiData(
    apiClient.POST("/api/v1/instrument-drivers/probe", {
      body: command,
    }),
  );
}

export async function openInstrumentSession(
  instrumentId: string,
  actor: string,
  setup: InstrumentList["setup"],
  operationId = createInstrumentCommandId("open"),
): Promise<InstrumentSession> {
  return apiData(
    apiClient.POST("/api/v1/instrument-sessions", {
      body: {
        setup,
        operation_id: operationId,
        actor,
        instrument_ids: [instrumentId],
        temporary_bindings: [],
      },
    }),
  );
}

export async function renewInstrumentSession(sessionId: string): Promise<InstrumentSessionLease> {
  return apiData(
    apiClient.POST("/api/v1/instrument-sessions/{session_id}/heartbeat", {
      params: { path: { session_id: sessionId } },
    }),
  );
}

export async function readInstrumentState(
  session: InstrumentSession,
  instrumentId: string,
): Promise<InstrumentState> {
  return apiData(
    apiClient.GET("/api/v1/instrument-sessions/{session_id}/instruments/{instrument_id}/state", {
      params: {
        path: {
          session_id: session.session_id,
          instrument_id: instrumentId,
        },
      },
    }),
  );
}

export async function readObservedInstrumentStateMembers(
  session: InstrumentSession,
  instrumentId: string,
  targets: InstrumentStateTarget[],
): Promise<InstrumentStateCache> {
  return apiData(
    apiClient.POST(
      "/api/v1/instrument-sessions/{session_id}/instruments/{instrument_id}/state/observed",
      {
        params: {
          path: { session_id: session.session_id, instrument_id: instrumentId },
        },
        body: { targets },
      },
    ),
  );
}

export async function readInstrumentStateMembers(
  session: InstrumentSession,
  instrumentId: string,
  targets: InstrumentStateTarget[],
): Promise<InstrumentStateReadback> {
  return apiData(
    apiClient.POST(
      "/api/v1/instrument-sessions/{session_id}/instruments/{instrument_id}/state/read",
      {
        params: {
          path: { session_id: session.session_id, instrument_id: instrumentId },
        },
        body: { targets },
      },
    ),
  );
}

export async function applyInstrumentState(
  session: InstrumentSession,
  instrumentId: string,
  members: StagedInstrumentMember[],
  commandId = createInstrumentCommandId("apply"),
): Promise<InstrumentApplyReceipt> {
  const [first, ...remaining] = members;
  if (!first) throw new Error("Apply requires at least one staged member.");
  const assignment = (member: StagedInstrumentMember) => ({
    resource_id: instrumentId,
    target: member.target,
    value: member.value,
  });
  return apiData(
    apiClient.POST(
      "/api/v1/instrument-sessions/{session_id}/instruments/{instrument_id}/state/apply",
      {
        params: {
          path: {
            session_id: session.session_id,
            instrument_id: instrumentId,
          },
        },
        body: {
          command_id: commandId,
          instrument_id: instrumentId,
          assignments: [assignment(first), ...remaining.map(assignment)],
        },
      },
    ),
  );
}

export async function applyInstrumentConfiguredDefaults(
  session: InstrumentSession,
  instrumentId: string,
  operationId = createInstrumentCommandId("configured-defaults"),
): Promise<InstrumentConfiguredDefaultsApplyReceipt> {
  return apiData(
    apiClient.POST(
      "/api/v1/instrument-sessions/{session_id}/instruments/{instrument_id}/configured-defaults/apply",
      {
        params: {
          path: {
            session_id: session.session_id,
            instrument_id: instrumentId,
          },
        },
        body: { operation_id: operationId },
      },
    ),
  );
}

export async function collectInstrumentAcquisition(
  session: InstrumentSession,
  instrumentId: string,
  target: InstrumentAcquisitionTarget,
  commandId = createInstrumentCommandId("collect"),
): Promise<InstrumentCollectReceipt> {
  const url = new URL(
    `/api/v1/instrument-sessions/${encodeURIComponent(session.session_id)}/instruments/${encodeURIComponent(instrumentId)}/collect`,
    globalThis.location?.origin ?? "http://localhost",
  );
  let response: Response;
  try {
    response = await globalThis.fetch(url, {
      method: "POST",
      headers: {
        Accept: HARDWARE_RECEIPT_MEDIA_TYPE,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        command_id: commandId,
        instrument_id: instrumentId,
        interface_id: target.interfaceId,
        component_path: target.componentPath,
        acquisition_id: target.acquisition.id,
        result_ids: [],
      }),
    });
  } catch {
    throw new ApiError("The local daemon did not respond.");
  }
  if (!response.ok) {
    const detail = await responseDetail(response);
    throw new ApiError(
      detail ?? `The daemon returned ${response.status} ${response.statusText}.`,
      response.status,
    );
  }
  try {
    return decodeCollectReceipt(await response.arrayBuffer());
  } catch {
    throw new ApiError("The daemon returned an invalid hardware receipt.");
  }
}

export async function invokeInstrumentOperation(
  session: InstrumentSession,
  instrumentId: string,
  target: InstrumentOperationTarget,
  arguments_: InstrumentOperationArgument[],
  commandId = createInstrumentCommandId("invoke"),
): Promise<InstrumentInvokeReceipt> {
  return apiData(
    apiClient.POST("/api/v1/instrument-sessions/{session_id}/instruments/{instrument_id}/invoke", {
      params: {
        path: {
          session_id: session.session_id,
          instrument_id: instrumentId,
        },
      },
      body: {
        command_id: commandId,
        instrument_id: instrumentId,
        resource_id: instrumentId,
        interface_id: target.interfaceId,
        component_path: target.componentPath,
        operation_id: target.operation.id,
        arguments: arguments_,
      },
    }),
  );
}

export async function closeInstrumentSession(sessionId: string, keepalive = false): Promise<void> {
  await endInstrumentSession(sessionId, "close", keepalive);
}

export async function abortInstrumentSession(sessionId: string): Promise<void> {
  await endInstrumentSession(sessionId, "abort");
}

async function endInstrumentSession(
  sessionId: string,
  action: "close" | "abort",
  keepalive = false,
): Promise<void> {
  const params = { path: { session_id: sessionId } };
  if (action === "close") {
    await apiData(
      apiClient.POST("/api/v1/instrument-sessions/{session_id}/close", {
        params,
        keepalive,
      }),
    );
    return;
  }
  await apiData(
    apiClient.POST("/api/v1/instrument-sessions/{session_id}/abort", {
      params,
      keepalive,
    }),
  );
}

export async function resolveInstrumentAttention(sessionId: string): Promise<void> {
  await apiData(
    apiClient.POST("/api/v1/instrument-sessions/{session_id}/attention", {
      params: { path: { session_id: sessionId } },
    }),
  );
}

export async function publishInstrumentSpec({
  revision,
  name,
  spec,
  originalInstrumentId,
  actor,
  note,
}: {
  revision: SetupRevision;
  name: string;
  spec: InstrumentSpec;
  originalInstrumentId?: string;
  actor: string;
  note: string;
}): Promise<SetupRevision> {
  const setup = structuredClone(revision.setup);
  const instruments = setup.instrument_registry.instruments;
  if (originalInstrumentId === undefined) {
    if (instruments.some((instrument) => instrument.id === spec.id)) {
      throw new Error(`The selected context already contains ${spec.id}.`);
    }
    instruments.push(spec);
  } else {
    const index = instruments.findIndex((instrument) => instrument.id === originalInstrumentId);
    if (index < 0) {
      throw new Error(`The selected context no longer contains ${originalInstrumentId}.`);
    }
    instruments[index] = spec;
  }
  return apiData(
    apiClient.POST("/api/v1/setup/revisions", {
      body: { revision_id: name, setup, actor, note },
    }),
  );
}

export function connectionSummary(connection: InstrumentView["connection"]): string {
  switch (connection.kind) {
    case "virtual":
      return "Virtual · local simulator";
    case "tcpip_socket":
      return `TCP/IP · ${connection.host}:${connection.port}`;
    case "serial":
      return `Serial · ${connection.port} @ ${connection.baud_rate}`;
    case "driver_managed":
      return "Driver managed";
  }
}

export function createInstrumentCommandId(prefix: string): string {
  const random =
    typeof globalThis.crypto?.randomUUID === "function"
      ? globalThis.crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `ui-${prefix}-${random}`;
}

export function retryTransientInstrumentMutation(failureCount: number, error: unknown): boolean {
  return failureCount < 1 && error instanceof ApiError && error.status === undefined;
}

async function responseDetail(response: Response): Promise<string | undefined> {
  try {
    const value: unknown = await response.json();
    return isObject(value) && typeof value.detail === "string" ? value.detail : undefined;
  } catch {
    return undefined;
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

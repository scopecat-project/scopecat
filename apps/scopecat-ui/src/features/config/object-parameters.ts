import type { MethodResponse } from "openapi-fetch";
import type { apiClient } from "../../api-client";
import type { ParameterDefinition, ParameterEntity, SampleView } from "../../api-contract";
import type { ParameterDraftAtom, ParameterDraftValue } from "./parameter-draft-api";

type Resolution = MethodResponse<typeof apiClient, "post", "/api/v1/measurement-context/resolve">;
export type ObjectParameterContext = {
  setupName?: string;
  sample: SampleView["revision"];
  resolution: Resolution;
};
export type ObjectParameterPanel = {
  node: HTMLElement;
  sampleId: string;
  revision: number;
  contentHash: string;
  entityId?: string;
};
export type ParameterWorkspaceHandle = {
  openObject: (context: ObjectParameterContext) => Promise<void>;
};

// A display name or an inline sample does not establish a target/setup mapping.
export function objectContextError({
  sample,
  resolution,
}: ObjectParameterContext): string | undefined {
  const { subject, target_binding: binding, parameters } = resolution.context;
  if (!resolution.branch || !("revision_id" in parameters))
    return "Choose a working parameter branch for object editing.";
  if (subject.kind !== "registered_target" || !binding || !resolution.setup)
    return "Choose a registered single-member target and resolve its setup mapping for object editing.";
  if (
    subject.sample.sample_id !== sample.sample_id ||
    subject.sample.revision !== sample.revision ||
    subject.sample.content_hash !== sample.content_hash
  )
    return "The resolved target belongs to another sample revision. Resolve this sample's context.";
  return undefined;
}

export function panelMatches(
  context: ObjectParameterContext,
  panel: ObjectParameterPanel,
): boolean {
  return (
    context.sample.sample_id === panel.sampleId &&
    context.sample.revision === panel.revision &&
    context.sample.content_hash === panel.contentHash
  );
}

export function mappedEntity(
  context: ObjectParameterContext,
  entityId?: string,
): ParameterEntity | undefined {
  if (objectContextError(context)) return;
  const { subject, target_binding: binding } = context.resolution.context;
  if (subject.kind !== "registered_target") return;
  const member = subject.content.members.find(
    (item) =>
      item.sample_id === context.sample.sample_id &&
      item.revision === context.sample.revision &&
      item.content_hash === context.sample.content_hash,
  );
  const entity = context.sample.content.topology?.entities?.find((item) => item.id === entityId);
  const projection = binding?.entities.find(
    (item) =>
      item.target_entity.member_id === member?.id && item.target_entity.entity_id === entityId,
  );
  return entity && projection ? { ...entity, id: projection.runtime_entity_id } : undefined;
}

export function rawEntity(atom?: ParameterDraftAtom): ParameterEntity | undefined {
  if (!atom) return;
  try {
    const value: unknown = JSON.parse(atom.text);
    if (
      value &&
      typeof value === "object" &&
      "id" in value &&
      typeof value.id === "string" &&
      (!("kind" in value) || value.kind === null || typeof value.kind === "string")
    )
      return { id: value.id, kind: "kind" in value ? (value.kind as string | null) : null };
  } catch {
    /* Incomplete raw input belongs to the full working table. */
  }
  return undefined;
}
export const entityKey = (entity: ParameterEntity) =>
  JSON.stringify([entity.kind ?? null, entity.id]);

export function objectRows(
  definitions: ParameterDefinition[],
  values: ParameterDraftValue[],
  entity: ParameterEntity,
) {
  return definitions.flatMap((definition) => {
    const table = definition.value_type;
    if (table.shape !== "table") return [];
    const value = values.find((item) => item.id === definition.id);
    if (value?.shape !== "table") return [];
    return (value.rows ?? []).flatMap((row, rowIndex) => {
      const references = table.columns
        .filter((column) => column.value_type.type === "entity")
        .flatMap((column) => {
          const ref = rawEntity(row[column.id]);
          return ref ? [ref] : [];
        });
      if (!references.some((ref) => entityKey(ref) === entityKey(entity))) return [];
      return [
        {
          definition,
          table,
          value,
          row,
          rowIndex,
          references: [...new Map(references.map((ref) => [entityKey(ref), ref])).values()],
        },
      ];
    });
  });
}

// The index is in the original raw array, never the filtered object presentation.
export function updateObjectCell(
  value: ParameterDraftValue,
  rowIndex: number,
  columnId: string,
  atom?: ParameterDraftAtom,
): ParameterDraftValue {
  return {
    ...value,
    rows: (value.rows ?? []).map((row, index) => {
      if (index !== rowIndex) return row;
      const next = { ...row };
      if (atom === undefined) delete next[columnId];
      else next[columnId] = atom;
      return next;
    }),
  };
}

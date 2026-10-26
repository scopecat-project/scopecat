import { navigate } from "../../lib/navigation";
import type { ObjectRef } from "./apparatus-api";

export function apparatusLocation(values: Record<string, string | number | undefined>) {
  const url = new URL(window.location.href);
  url.hash = "history";
  url.searchParams.set("data", "apparatus");
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === "") url.searchParams.delete(`apparatus-${key}`);
    else url.searchParams.set(`apparatus-${key}`, String(value));
  }
  navigate(url);
}

export function selectObject(space: string, ref: ObjectRef) {
  apparatusLocation({
    space,
    catalog: ref.catalog_id,
    object: ref.object_id,
    revision: ref.revision,
    hash: ref.content_hash,
    before: undefined,
    observation: undefined,
  });
}

export function selectedRef(params: URLSearchParams): ObjectRef | undefined {
  const catalog_id = params.get("apparatus-catalog");
  const object_id = params.get("apparatus-object");
  const revision = Number(params.get("apparatus-revision"));
  const content_hash = params.get("apparatus-hash");
  if (!catalog_id || !object_id || !Number.isSafeInteger(revision) || revision < 1 || !content_hash)
    return undefined;
  return { catalog_id, object_id, revision, content_hash };
}

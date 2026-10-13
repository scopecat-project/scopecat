import type { ProjectRun } from "../../types";

// Names are for reading; exact identity remains available for disambiguation.
export function RunAddressLabel({
  address,
  sequence,
  collectionName,
}: Pick<ProjectRun, "address" | "sequence"> & { collectionName?: string }) {
  const identity = [
    address && `Collection ID: ${address.collection_id}`,
    sequence !== undefined && `Global #${sequence}`,
    address && `Run ID: ${address.run_id}`,
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <span
      className="block cursor-help break-words text-xs text-text-soft"
      data-testid="run-address"
      title={identity}
    >
      {address ? (
        <>
          {collectionName ?? "Collection name unavailable"} · #{address.number}
        </>
      ) : (
        "Collection address unavailable"
      )}
    </span>
  );
}

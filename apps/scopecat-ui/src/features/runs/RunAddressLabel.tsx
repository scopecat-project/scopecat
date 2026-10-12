import type { ProjectRun } from "../../types";

// Names can change or collide; always retain the namespace beside the short number.
export function RunAddressLabel({
  address,
  sequence,
  collectionName,
}: Pick<ProjectRun, "address" | "sequence"> & { collectionName?: string }) {
  return (
    <span className="block break-words text-xs text-text-soft" data-testid="run-address">
      {address ? (
        <>
          Collection <code className="break-all">{collectionName ?? address.collection_id}</code> ·
          #{address.number}
          {collectionName && <span className="text-text-dim"> ({address.collection_id})</span>}
        </>
      ) : (
        "Collection address unavailable"
      )}
      {sequence !== undefined && (
        <span className="inline-block whitespace-nowrap text-text-dim"> · Global #{sequence}</span>
      )}
    </span>
  );
}

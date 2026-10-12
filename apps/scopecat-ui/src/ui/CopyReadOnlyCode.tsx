import { useEffect, useRef, useState } from "react";
import { readOnlyCode, type ReadOnlyCopyTarget } from "../lib/read-only-code";
import { secondaryButton } from "./styles";

export function CopyReadOnlyCode({
  target,
  compact = false,
}: {
  target: ReadOnlyCopyTarget;
  compact?: boolean;
}) {
  const code = readOnlyCode(target);
  // A different identity gets fresh state, including after a pending clipboard write.
  return <CopyCode key={code} code={code} compact={compact} />;
}

export function CopyRunReference({
  runId,
  address,
}: {
  runId: string;
  address: { collection_id: string; number: number };
}) {
  const reference = `${address.collection_id} #${address.number} (run ${runId})`;
  return <CopyCode key={reference} code={reference} compact kind="reference" />;
}

function CopyCode({
  code,
  compact,
  kind = "code",
}: {
  code: string;
  compact: boolean;
  kind?: "code" | "reference";
}) {
  const reference = kind === "reference";
  const [status, setStatus] = useState<"idle" | "copying" | "copied" | "manual">("idle");
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  async function copy() {
    setStatus("copying");
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(code);
      if (mounted.current) setStatus("copied");
    } catch {
      if (mounted.current) setStatus("manual");
    }
  }

  return (
    <div
      className={compact ? "grid min-w-0 gap-2 text-xs" : "mt-2 grid min-w-0 gap-2 text-[0.65rem]"}
    >
      <button
        className={secondaryButton}
        title={
          reference
            ? "Copy the collection ID, local number and stable run ID in this data space"
            : "Copy a read-only Notebook snippet for this exact saved record"
        }
        type="button"
        disabled={status === "copying"}
        onClick={copy}
      >
        {status === "copying"
          ? "Copying…"
          : reference
            ? "Copy run reference"
            : "Copy read-only code"}
      </button>
      {!reference && (!compact || status !== "idle") && (
        <p className="m-0 text-text-dim">
          First run the connection cell for this data space. Paste into a separate cell and run only
          that cell, not Run All. Custom typed/group objects are not reconstructed.
        </p>
      )}
      {status === "copied" && (
        <p className="m-0 text-text-soft" role="status">
          {reference ? "Run reference copied." : "Read-only code copied."}
        </p>
      )}
      {status === "manual" && (
        <>
          <p className="m-0 text-text-soft" role="status">
            Clipboard unavailable. Select and copy the {reference ? "reference" : "code"} below.
          </p>
          <textarea
            aria-label={reference ? "Run reference" : "Read-only code"}
            className="w-full min-w-0 rounded border border-line bg-panel p-2 font-mono text-text-soft"
            readOnly
            rows={code.split("\n").length + 1}
            value={code}
            onFocus={(event) => event.currentTarget.select()}
          />
        </>
      )}
    </div>
  );
}

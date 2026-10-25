import * as Controls from "./Controls";
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
  collectionName,
}: {
  runId: string;
  address: { collection_id: string; number: number };
  collectionName?: string;
}) {
  const reference = `${collectionName ?? "Collection"} #${address.number} (collection ${address.collection_id}; run ${runId})`;
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
    <div className={compact ? "grid min-w-0 gap-2 text-xs" : "mt-2 grid min-w-0 gap-2 text-xs"}>
      <button
        className={secondaryButton}
        title={
          reference
            ? "复制此数据空间中的记录集 ID、本地编号与稳定 run ID"
            : "复制此精确记录的只读 Notebook 代码"
        }
        type="button"
        disabled={status === "copying"}
        onClick={copy}
      >
        {status === "copying" ? "正在复制…" : reference ? "复制 run 引用" : "复制只读代码"}
      </button>
      {!reference && (!compact || status !== "idle") && (
        <p className="m-0 text-text-dim">
          First run the connection cell for this data space. Paste into a separate cell and run only
          that cell, not Run All. Custom typed/group objects are not reconstructed.
        </p>
      )}
      {status === "copied" && (
        <p className="m-0 text-text-soft" role="status">
          {reference ? "run 引用已复制。" : "只读代码已复制。"}
        </p>
      )}
      {status === "manual" && (
        <>
          <p className="m-0 text-text-soft" role="status">
            Clipboard unavailable. Select and copy the {reference ? "reference" : "code"} below.
          </p>
          <Controls.Textarea
            aria-label={reference ? "run 引用" : "只读代码"}
            className="w-full min-w-0 font-mono"
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

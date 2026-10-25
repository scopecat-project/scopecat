import { Field, Input, Button, Notice } from "../../ui/Controls";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError } from "../../api-client";
import { getRun } from "../runs/run-api";
import { RunAddressLabel } from "../runs/RunAddressLabel";
import { useRunCollectionName } from "../runs/use-run-collection-name";

// Matches the server's safe storage-reference grammar, including retained legacy
// IDs. A syntactically valid string is not evidence that a record exists.
export const validRunReference = (value: string) => /^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(value);

export function RunIdLookup({
  projectId,
  unavailable,
  names,
  onOpenRun,
}: {
  projectId: string;
  unavailable: boolean;
  names: Map<string, string>;
  onOpenRun: (id: string) => void;
}) {
  const [input, setInput] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [invalid, setInvalid] = useState(false);
  const result = useQuery({
    queryKey: ["run-id-lookup", projectId, submitted],
    enabled: Boolean(submitted && !unavailable),
    retry: false,
    queryFn: ({ signal }) => getRun(submitted, signal),
  });
  const collection = useRunCollectionName(
    projectId,
    unavailable ? undefined : result.data?.address?.collection_id,
    names,
  );
  const error = result.error;
  return (
    <section aria-label="按完整 run ID 查找" className="min-w-0 rounded border border-line p-3">
      <details>
        <summary className="cursor-pointer text-sm">按完整 run ID 查找</summary>
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const value = input.trim();
            setInvalid(!validRunReference(value));
            if (!validRunReference(value)) {
              setSubmitted("");
              return;
            }
            if (value === submitted) void result.refetch();
            else setSubmitted(value);
          }}
        >
          <Field className="min-w-0 flex-1 basis-72">
            完整 run ID
            <Input
              aria-label="完整 run ID"
              aria-invalid={invalid}
              className="mt-1 w-full min-w-0"
              value={input}
              onChange={(event) => {
                setInput(event.target.value);
                setSubmitted("");
                setInvalid(false);
              }}
              placeholder="粘贴完整 ID"
            />
          </Field>
          <Button disabled={unavailable || result.isFetching}>查找精确 run</Button>
        </form>
        <p className="mt-1 text-xs text-text-dim">
          仅在当前数据空间查找，不受记录集和列表筛选影响。
        </p>
        {invalid && (
          <Notice role="alert">run ID 格式无效。请粘贴完整 ID，不要输入 URL 或路径。</Notice>
        )}
        {unavailable ? (
          <Notice role="status">run 查找不可用，请重新连接此数据空间。</Notice>
        ) : (
          submitted && (
            <>
              {(result.isPending || result.isFetching) && (
                <Notice role="status">正在查找 run…</Notice>
              )}
              {error && !result.isFetching && (
                <Notice role="alert">
                  {error instanceof ApiError && error.status === 404
                    ? "当前数据空间未找到此 run，请核实完整 ID 与数据空间。"
                    : error instanceof ApiError && (error.status === 400 || error.status === 422)
                      ? `run 引用无效： ${error.message}`
                      : `run 查找不可用： ${error.message}`}
                </Notice>
              )}
              {result.data && !error && !result.isFetching && (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <RunAddressLabel
                    address={result.data.address}
                    sequence={result.data.sequence}
                    collectionName={collection.name}
                  />
                  <Button onClick={() => onOpenRun(result.data.runId)}>打开找到的 run</Button>
                </div>
              )}
            </>
          )
        )}
      </details>
    </section>
  );
}

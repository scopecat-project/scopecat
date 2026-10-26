import * as Controls from "../../ui/Controls";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getDecisionDraftHistory } from "./decision-api";
import { errorMessage } from "../../lib/presentation";

export function DecisionDraftHistory() {
  const [open, setOpen] = useState(false);
  const [before, setBefore] = useState<number>();
  const history = useQuery({
    queryKey: ["decision-draft-history", before],
    queryFn: () => getDecisionDraftHistory(before),
    enabled: open,
    refetchInterval: 2000,
  });
  return (
    <section className="mb-3 rounded-md border border-line bg-panel p-3">
      <Controls.Button type="button" onClick={() => setOpen(!open)}>
        恢复已保存的判断草稿
      </Controls.Button>
      {open && (
        <div className="grid gap-2 text-sm">
          <p>
            此处保留编辑历史，包括冲突副本和已丢弃的草稿，不代表已记录的科学判断。对于已完成或已变化的请求，请在任务中核实；恢复历史不能提交判断或继续执行。如需复用内容，请手动复制。
          </p>
          {history.isError && <p role="alert">无法读取草稿历史：{errorMessage(history.error)}</p>}
          {history.data?.items.map((draft) => (
            <details key={draft.revision}>
              <summary>
                {draft.target.procedure_run_id} · {draft.target.step_key} · 尝试{" "}
                {draft.target.attempt} ·
                {{ saved: "已保存", conflict: "冲突", discarded: "已丢弃" }[draft.state]} · 版本{" "}
                {draft.revision}
              </summary>
              <p>
                保存于 {draft.created_at}；原请求 {draft.baseline.request_hash}
              </p>
              <Controls.Textarea
                aria-label={`保留的草稿 ${draft.revision}`}
                readOnly
                rows={8}
                className="w-full font-mono"
                value={JSON.stringify(draft.input, null, 2)}
              />
            </details>
          ))}
          {history.data?.next_cursor && (
            <Controls.Button type="button" onClick={() => setBefore(history.data.next_cursor!)}>
              更早的草稿
            </Controls.Button>
          )}
          {before && (
            <Controls.Button type="button" onClick={() => setBefore(undefined)}>
              最新草稿
            </Controls.Button>
          )}
        </div>
      )}
    </section>
  );
}

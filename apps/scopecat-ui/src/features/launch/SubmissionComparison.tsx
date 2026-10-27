import { useState } from "react";
import { Button } from "../../ui/Controls";
import type { LaunchDraft } from "./LaunchDraft";
import type { SubmissionRequest } from "./launch-submission";
import { compareSubmission, submittedInputRows } from "./submission-comparison";

export function SubmissionComparison({
  request,
  draft,
}: {
  request: SubmissionRequest;
  draft?: LaunchDraft;
}) {
  const [showUnchanged, setShowUnchanged] = useState(false);
  if (!draft)
    return (
      <details className="border-t border-line pt-2">
        <summary className="cursor-pointer font-semibold">已提交输入</summary>
        <p className="my-2">选择草稿以与此已提交请求比较。</p>
        <dl className="space-y-2">
          {submittedInputRows(request).map((row) => (
            <div key={row.label}>
              <dt className="font-medium">{row.label}</dt>
              <dd className="whitespace-pre-wrap break-words">{row.value}</dd>
            </div>
          ))}
        </dl>
      </details>
    );
  const rows = compareSubmission(request, draft);
  const changed = rows.filter((row) => row.status === "Changed").length;
  const mismatch =
    request.workspace_id !== draft.workspaceId || request.experiment !== draft.experiment;
  const order = { Changed: 0, "Incomplete current input": 1, "Not compared": 2, Same: 3 };
  const visible = rows
    .filter((row) => showUnchanged || row.status !== "Same")
    .sort((a, b) => order[a.status] - order[b.status]);
  return (
    <details className="border-t border-line pt-2">
      <summary className="cursor-pointer font-semibold">比较已提交请求与当前草稿</summary>
      <p className="mt-2 text-sm text-text-dim">
        将所选已提交请求与当前草稿“{draft.draftName ?? "草稿"}”比较。
        这不表示当前草稿就是最初提交的草稿。编辑此草稿不会改变已提交工作。
      </p>
      {mismatch && (
        <p className="mt-2 text-yellow">当前选择了不同的工作区或实验。这是相互独立的准备。</p>
      )}
      <p className="my-2">
        {changed} 个字段已变化。默认值和未完整输入不会判为相同。
        此处仅作描述性比较，不触发预览或执行；开始时仍会在提交前核实输入。
      </p>
      <Button
        type="button"
        onClick={() => setShowUnchanged((value) => !value)}
        aria-expanded={showUnchanged}
      >
        {showUnchanged ? "隐藏未变化字段" : "显示未变化字段"}
      </Button>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full table-fixed text-left text-sm">
          <caption className="sr-only">所选已提交请求与当前草稿对比</caption>
          <thead>
            <tr>
              <th scope="col" className="w-1/4 p-2">
                输入
              </th>
              <th scope="col" className="p-2">
                所选已提交请求
              </th>
              <th scope="col" className="p-2">
                当前草稿 · {draft.draftName ?? "草稿"}
              </th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr key={row.label} className="border-t border-line align-top">
                <th scope="row" className="break-words p-2 font-medium">
                  {row.label}
                  <span className="block text-xs font-normal text-text-dim">
                    {comparisonStatusLabel[row.status]}
                  </span>
                </th>
                <td className="whitespace-pre-wrap break-words p-2 [overflow-wrap:anywhere]">
                  {row.submitted}
                </td>
                <td className="whitespace-pre-wrap break-words p-2 [overflow-wrap:anywhere]">
                  {row.current}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

const comparisonStatusLabel = {
  Changed: "已变化",
  Same: "相同",
  "Incomplete current input": "当前输入未完整",
  "Not compared": "未比较",
};

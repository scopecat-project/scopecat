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
        <summary className="cursor-pointer font-semibold">Submitted inputs</summary>
        <p className="my-2">Select a draft to compare with this submitted request.</p>
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
      <summary className="cursor-pointer font-semibold">
        Compare submitted request with current draft
      </summary>
      <p className="mt-2 text-sm text-text-dim">
        Selected submitted request compared with current draft “{draft.draftName ?? "Draft"}”. This
        does not identify the draft originally submitted. Editing this draft does not change
        submitted work.
      </p>
      {mismatch && (
        <p className="mt-2 text-yellow">
          Different workspace or experiment selected. These are separate preparations.
        </p>
      )}
      <p className="my-2">
        {changed} changed {changed === 1 ? "field" : "fields"}. Defaults and incomplete inputs are
        not treated as equal. Start still checks inputs before submission.
      </p>
      <Button
        type="button"
        onClick={() => setShowUnchanged((value) => !value)}
        aria-expanded={showUnchanged}
      >
        {showUnchanged ? "Hide unchanged fields" : "Show unchanged fields"}
      </Button>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full table-fixed text-left text-sm">
          <caption className="sr-only">
            Selected submitted request and current draft comparison
          </caption>
          <thead>
            <tr>
              <th scope="col" className="w-1/4 p-2">
                Input
              </th>
              <th scope="col" className="p-2">
                Selected submitted request
              </th>
              <th scope="col" className="p-2">
                当前草稿 · {draft.draftName ?? "Draft"}
              </th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr key={row.label} className="border-t border-line align-top">
                <th scope="row" className="break-words p-2 font-medium">
                  {row.label}
                  <span className="block text-xs font-normal text-text-dim">{row.status}</span>
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

import { navigate } from "../../lib/navigation";
import type { components } from "../../api-schema";
import type { LaunchPreview } from "./launch-api";
import { useLaunchDraft } from "./LaunchDraft";
import { CompiledInspectionView } from "../inspections/CompiledInspectionView";
import { Button } from "../../ui/Controls";

export type PreviewRequest = components["schemas"]["LaunchRequest-Input"];
export interface FrozenLaunchPreview {
  id: number;
  projectId: string | undefined;
  draftId: string | undefined;
  draftRevision: number;
  definition: string;
  request: PreviewRequest;
  preview: LaunchPreview;
}

// Session-only evidence, separate from the draft's admission-bearing preview.
export function FrozenLaunchPreviewPanel() {
  const { draft, frozenPreviews, openFrozenPreview, setOpenFrozenPreview } = useLaunchDraft();
  if (!frozenPreviews.length) return null;
  const frozen = frozenPreviews.find((item) => item.id === openFrozenPreview);
  const preview = frozen?.preview;
  const inspection = preview?.inspection;
  const samePreparation =
    frozen &&
    draft &&
    draft.revision === frozen.draftRevision &&
    draft.draftId === frozen.draftId &&
    draft.workspaceId === preview?.workspace_id &&
    draft.definition === frozen.definition &&
    (!draft.preview ||
      (draft.preview.request_hash === preview?.request_hash &&
        JSON.stringify(draft.preview.code_revision) === JSON.stringify(preview?.code_revision) &&
        JSON.stringify(draft.preview.reviewed) === JSON.stringify(preview?.reviewed)));
  return (
    <section
      aria-label="保留的预览检查"
      className="min-w-0 space-y-3 rounded-md border border-line p-3"
    >
      <h3 className="font-semibold">保留的预览检查</h3>
      <p className="text-sm">
        仅保留在本次项目会话中。查看不会再次编译；显式预览仍会执行检查计算并记录必要的准备信息。冻结结果不能作为当前提交资格。
      </p>
      <div className="flex flex-wrap gap-2">
        {frozenPreviews.map((item) => (
          <Button key={item.id} type="button" onClick={() => setOpenFrozenPreview(item.id)}>
            查看检查 {item.id}
          </Button>
        ))}
      </div>
      {frozen && preview && (
        <section aria-label={`冻结检查 ${frozen.id}`} className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4>
              检查 {frozen.id} · {preview.experiment_id}
            </h4>
            <Button type="button" onClick={() => setOpenFrozenPreview(undefined)}>
              关闭检查视图
            </Button>
          </div>
          <p role="status">
            {samePreparation
              ? "保留自当前准备；是否可提交仍由当前预览检查决定。"
              : "与当前准备不同：此结果保持原基线，不随准备修改。"}
          </p>
          <dl className="grid gap-1 text-sm [overflow-wrap:anywhere]">
            <dt>数据空间</dt>
            <dd>{frozen.projectId ?? "未知"}</dd>
            <dt>工作区</dt>
            <dd>{preview.workspace_id}</dd>
            <dt>代码版本（独立于请求哈希）</dt>
            <dd>{preview.code_revision?.content_hash ?? "未知"}</dd>
            <dt>定义哈希</dt>
            <dd>{preview.definition_hash ?? "未知"}</dd>
            <dt>请求哈希</dt>
            <dd>{preview.request_hash}</dd>
          </dl>
          {frozen.draftId && (
            <a
              className="text-accent underline"
              onClick={(event) => {
                event.preventDefault();
                navigate(event.currentTarget.getAttribute("href")!);
              }}
              href={`?${new URLSearchParams({ workspace: preview.workspace_id, experiment: preview.experiment_id, draft: frozen.draftId })}#launch`}
            >
              返回原准备草稿（不恢复预览资格）
            </a>
          )}
          <details>
            <summary>冻结的配置、setup 与扫描输入</summary>
            <pre className="max-h-80 overflow-auto text-xs">
              {JSON.stringify(
                {
                  definition: frozen.definition,
                  procedure_definition: preview.procedure_definition,
                  reviewed: preview.reviewed,
                  scan_mode: frozen.request.scan_mode,
                  parameter_sweeps: frozen.request.parameter_sweeps,
                  inputs: frozen.request.inputs,
                  control_edits: frozen.request.control_edits,
                  resolved_inputs: preview.resolved_inputs,
                  controls: preview.controls,
                },
                null,
                2,
              )}
            </pre>
          </details>
          <p className="text-sm">
            这是有界检查，不保证包含完整下发波形。总点数：{inspection?.total_point_count ?? "未知"}
            ；显示点数：{inspection?.points.length ?? "未知"}。
          </p>
          <p className="text-sm">
            截断：
            {!inspection
              ? "未知"
              : [inspection.points_truncated ? "扫描点" : "", ...inspection.truncated]
                  .filter(Boolean)
                  .join("、") || "未报告集合截断（目标内容仍受各自预算限制）"}
            。
          </p>
          <details>
            <summary>选中点（目标检查仅适用于此点）</summary>
            <pre className="max-h-48 overflow-auto text-xs">
              {inspection?.selected_point
                ? JSON.stringify(inspection.selected_point, null, 2)
                : "未知／未提供"}
            </pre>
          </details>
          <CompiledInspectionView
            key={frozen.id}
            inspections={inspection?.domain_inspections ?? []}
            emptyTitle="此预览未提供目标波形检查；范围未知"
          />
        </section>
      )}
    </section>
  );
}

/* oxlint-disable jsx-a11y/no-noninteractive-tabindex -- Named scroll regions need keyboard access. */
import type { components } from "../../api-schema";
import type { LaunchPreview } from "./launch-api";
import { PlannedSettings } from "./PlannedSettings";

type Quantity =
  | components["schemas"]["ExactQuantity"]
  | components["schemas"]["BoundedQuantity"]
  | components["schemas"]["UnknownQuantity"];

function QuantityValue({ quantity }: { quantity: Quantity }) {
  return (
    <span className="block" title={quantity.basis}>
      {quantity.kind === "exact"
        ? `精确： ${quantity.value} ${quantity.unit}`
        : quantity.kind === "bounded"
          ? `有界： ${quantity.lower}–${quantity.upper} ${quantity.unit}`
          : `未知（${quantity.unit}）`}
      <span className="block text-xs text-muted-foreground">{quantity.basis}</span>
    </span>
  );
}
const costLabels: Record<string, string> = {
  wall_time: "总耗时",
  waveform_playback_time: "波形播放时长",
  waveform_bytes: "波形缓冲区",
  result_bytes: "结果缓冲区",
  retained_bytes: "保留数据集大小",
  batches: "总批次数",
  batch_point_capacity: "每批点数容量",
};

export function PreflightSummary({ preview }: { preview: LaunchPreview }) {
  return (
    <div className="min-w-0 space-y-3 [overflow-wrap:anywhere]">
      <h3 role="status" className="font-semibold">
        预览就绪
      </h3>
      <p>{preview.summary}</p>
      <p>
        首个实验初始点：{preview.point_count} · 配置{" "}
        {preview.reviewed.config_source.kind === "analysis_candidate"
          ? `候选 ${preview.reviewed.config_source.proposal_id}`
          : `参数 ${preview.reviewed.config_source.parameters.revision_id} / setup ${preview.reviewed.config_source.setup.revision_id}`}
      </p>
      {!preview.preflight ? (
        <p>未提供详细预检信息。流程范围、重复采集次数和成本估算均未知。</p>
      ) : (
        <details className="rounded-md border border-line p-3 space-y-3">
          <summary className="cursor-pointer font-semibold">估算与计划测量</summary>
          <p>{preview.preflight.scope_basis}</p>
          {preview.preflight.stages.map((stage) => (
            <section
              key={stage.id}
              aria-label={stage.label}
              className="border rounded p-3 space-y-2"
            >
              <h4 className="font-semibold">{stage.label}</h4>
              <p>{stage.configuration_meaning}</p>
              <dl className="grid grid-cols-2 gap-2 text-sm">
                <dt>选中实体</dt>
                <dd>{stage.entity_ids === null ? "未知" : stage.entity_ids.join(", ") || "无"}</dd>
                <dt>计划执行次数</dt>
                <dd>
                  <QuantityValue quantity={stage.executions} />
                </dd>
                <dt>{stage.point_scope === "static_plan" ? "每次执行计划点数" : "可能采集点数"}</dt>
                <dd>
                  <QuantityValue quantity={stage.points_per_execution} />
                </dd>
                <dt>初始建议点数</dt>
                <dd>{stage.initial_proposed_points}</dd>
                <dt>每个实体每点重复采集次数</dt>
                <dd>
                  <QuantityValue quantity={stage.shots_per_point_per_entity} />
                </dd>
              </dl>
              <p className="text-sm">
                预览预算： {stage.sampled_points}/{stage.sampled_point_limit} 个展示点；{" "}
                {stage.selected_points}/{stage.selected_point_limit} 个选中点已检查。
              </p>
              <div
                role="region"
                aria-label="产物表格"
                tabIndex={0}
                className="min-w-0 max-w-full overflow-x-auto [overflow-wrap:normal]"
              >
                <table className="w-full min-w-96 text-left text-sm [&_th]:whitespace-nowrap [&_td]:whitespace-nowrap [&_th]:px-2 [&_td]:px-2">
                  <caption className="text-left font-medium">产物与保留策略</caption>
                  <thead>
                    <tr>
                      <th className="text-left">产物</th>
                      <th>保留策略</th>
                      <th>维度 / 形状（范围见下方）</th>
                      <th>类型 / 单位</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stage.products.map((product) => (
                      <tr key={`${product.retention}:${product.id}`}>
                        <td>{product.id}</td>
                        <td>
                          {product.retention === "retained" ? "保留（计划数据集）" : "临时（每点）"}
                        </td>
                        <td>
                          {product.dims.length
                            ? product.dims
                                .map((dim, i) => `${dim}: ${product.shape[i] ?? "未知"}`)
                                .join(" × ")
                            : "标量"}
                        </td>
                        <td>
                          {product.dtype}
                          {product.unit ? ` / ${product.unit}` : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="text-sm space-y-2">
                {stage.costs.map((cost, i) => (
                  <li key={`${cost.metric}:${i}`}>
                    <strong>{costLabels[cost.metric] ?? cost.metric}</strong> (
                    {cost.scope === "inspected_artifact"
                      ? "仅已检查产物"
                      : cost.scope === "experiment"
                        ? "实验"
                        : "流程"}
                    ){cost.target_id && <span> · 目标： {cost.target_id}</span>}
                    <QuantityValue quantity={cost.quantity} />
                  </li>
                ))}
              </ul>
              <PlannedSettings stage={stage} />
              <details>
                <summary>选点检查与配置指纹</summary>
                <pre className="overflow-auto text-xs p-3">
                  {JSON.stringify(
                    {
                      config_content_hash: stage.config_content_hash,
                      inspections: stage.inspections,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            </section>
          ))}
        </details>
      )}
      <details>
        <summary>已解析输入与配置</summary>
        <pre className="overflow-auto text-xs p-3">
          {JSON.stringify({ inputs: preview.resolved_inputs, reviewed: preview.reviewed }, null, 2)}
        </pre>
      </details>
    </div>
  );
}

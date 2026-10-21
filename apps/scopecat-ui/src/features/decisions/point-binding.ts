import { analysisSeriesId } from "../runs/analysis-series-id";
import type { AnalysisOutput } from "../../types";
import { decisionFields, isRecord } from "./DecisionFields";

// Presentation metadata of the existing interpretation request. The input index
// addresses the step's exact retained evidence, never a latest-analysis lookup.
export interface PointBinding {
  input: number;
  output: string;
  layer: string;
  column: string;
  field: string;
  unit: string | null;
}
export interface FigurePoint {
  seriesId: string;
  dataIndex: number;
}

const text = (value: unknown): value is string => typeof value === "string" && value.length > 0;

export function pointBinding(metadata: unknown, structure: unknown): PointBinding | undefined {
  if (!isRecord(metadata) || !isRecord(metadata.point_binding)) return;
  const binding = metadata.point_binding;
  if (
    typeof binding.input !== "number" ||
    !Number.isInteger(binding.input) ||
    binding.input < 0 ||
    !text(binding.output) ||
    !text(binding.layer) ||
    !text(binding.column) ||
    !text(binding.field) ||
    !(binding.unit === null || (typeof binding.unit === "string" && binding.unit.length > 0))
  )
    return;
  const field = decisionFields(structure)?.[binding.field];
  if (!field || (field.type !== "float" && field.type !== "int")) return;
  return {
    input: binding.input,
    output: binding.output,
    layer: binding.layer,
    column: binding.column,
    field: binding.field,
    unit: binding.unit,
  };
}

export function bindingProblem(binding: PointBinding, output: AnalysisOutput): string | undefined {
  if (output.id !== binding.output || output.kind !== "figure")
    return "The declared figure is unavailable.";
  const layer = output.content.layers.find((item) => item.id === binding.layer);
  if (!layer) return "The declared figure layer is unavailable.";
  const axis =
    layer.projection.x === binding.column
      ? "x"
      : layer.projection.y === binding.column
        ? "y"
        : undefined;
  if (!axis) return "The declared data column is not plotted in this layer.";
  if ((layer.preview[`${axis}_axis`].unit ?? null) !== binding.unit)
    return "The declared field unit does not match the plotted column.";
  return undefined;
}

export function boundPointValue(
  binding: PointBinding,
  output: AnalysisOutput,
  point: FigurePoint,
): number | undefined {
  if (
    bindingProblem(binding, output) ||
    output.kind !== "figure" ||
    !Number.isInteger(point.dataIndex) ||
    point.dataIndex < 0
  )
    return;
  const layer = output.content.layers.find((item) => item.id === binding.layer)!;
  const series = layer.preview.series.find(
    (item) => analysisSeriesId(layer.id, item.id) === point.seriesId,
  );
  const axis = layer.projection.x === binding.column ? "x" : "y";
  const value = series?.[axis][point.dataIndex];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

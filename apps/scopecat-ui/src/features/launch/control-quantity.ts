import registry from "../../unit-registry.json";
const units: Record<string, { kind: string; scale: number | null }> = registry;
const bases: Record<string, string> = {
  frequency: "Hz",
  time: "s",
  current: "A",
  voltage: "V",
  resistance: "Ohm",
  temperature: "K",
  power: "W",
};
const prefixes = new Set(["G", "M", "k", "m", "u", "n"]);
/** Parse once and retain the decimal spelling until the final target conversion. */
function parseControl(text: string, unit: string | null): { numeric: string; unit: string | null } {
  const match = /^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([^\s]*)\s*$/.exec(text);
  if (!match || !Number.isFinite(Number(match[1]))) throw new Error("控制值必须是有限数值。");
  let suffix = match[2]!.replaceAll("µ", "u").replaceAll("μ", "u");
  if (!suffix) return { numeric: match[1]!, unit };
  const target = units[unit ?? ""];
  if (target && bases[target.kind] && target.scale !== null && prefixes.has(suffix))
    suffix += bases[target.kind];
  if (suffix !== unit) {
    const source = units[suffix];
    if (
      !target ||
      !source ||
      target.kind !== source.kind ||
      target.scale === null ||
      source.scale === null
    )
      throw new Error(`未知或不兼容的单位：${match[2]}。`);
  }
  return { numeric: match[1]!, unit: suffix };
}

function scaleControl(numeric: string, from: string | null, to: string | null): number {
  if (from === to || (!from && !to)) return Number(numeric);
  const source = units[from ?? ""],
    target = units[to ?? ""];
  if (
    !source ||
    !target ||
    source.kind !== target.kind ||
    source.scale === null ||
    target.scale === null
  )
    throw new Error("请选择兼容的单位。");
  const sourceExponent = Math.round(Math.log10(source.scale));
  const targetExponent = Math.round(Math.log10(target.scale));
  let result: number;
  if (10 ** sourceExponent === source.scale && 10 ** targetExponent === target.scale) {
    // Engineering prefixes are exact decimal shifts, not floating-point multiplies.
    // Convert to the API's number once, after moving the exponent; no epsilon/range relaxation.
    const [mantissa, exponent = "0"] = numeric.toLowerCase().split("e");
    result = Number(`${mantissa}e${Number(exponent) + sourceExponent - targetExponent}`);
  } else {
    result = (Number(numeric) * source.scale) / target.scale;
  }
  if (!Number.isFinite(result)) throw new Error("控制值必须是有限数值。");
  return result;
}
/** Only registry units; bare prefixes are relative to the unprefixed unit. */
export function controlNumber(text: string, unit: string | null): number {
  const parsed = parseControl(text, unit);
  return scaleControl(parsed.numeric, parsed.unit, unit);
}
export function convertControlNumber(text: string, from: string | null, to: string | null): string {
  const parsed = parseControl(text, from);
  // Validate the declared display units as well as a supplied suffix.
  if (from !== to) scaleControl("0", from, to);
  return String(scaleControl(parsed.numeric, parsed.unit, to));
}

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
/** Only registry units; bare prefixes are relative to the unprefixed unit. */
export function controlNumber(text: string, unit: string | null): number {
  const match = /^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([^\s]*)\s*$/.exec(text);
  if (!match || !Number.isFinite(Number(match[1])))
    throw new Error("Control values must be finite numbers.");
  const value = Number(match[1]);
  let suffix = match[2]!.replaceAll("µ", "u").replaceAll("μ", "u");
  if (!suffix) return value;
  const target = units[unit ?? ""];
  if (target && bases[target.kind] && target.scale !== null && prefixes.has(suffix))
    suffix += bases[target.kind];
  if (suffix === unit) return value;
  const source = units[suffix];
  if (
    !target ||
    !source ||
    target.kind !== source.kind ||
    target.scale === null ||
    source.scale === null
  )
    throw new Error(`Unknown or incompatible unit: ${match[2]}.`);
  const result = (value * source.scale) / target.scale;
  if (!Number.isFinite(result)) throw new Error("Control values must be finite numbers.");
  return result;
}
export function convertControlNumber(text: string, from: string | null, to: string | null): string {
  const value = controlNumber(text, from);
  if (from === to || (!from && !to)) return String(value);
  const source = units[from ?? ""],
    target = units[to ?? ""];
  if (
    !source ||
    !target ||
    source.kind !== target.kind ||
    source.scale === null ||
    target.scale === null
  )
    throw new Error("Choose a compatible unit.");
  const result = (value * source.scale) / target.scale;
  if (!Number.isFinite(result)) throw new Error("Control values must be finite numbers.");
  return String(result);
}

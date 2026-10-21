// Tuple encoding keeps arbitrary retained layer/series names unambiguous.
export function analysisSeriesId(layer: string, series: string, part = "data"): string {
  return JSON.stringify([layer, series, part]);
}

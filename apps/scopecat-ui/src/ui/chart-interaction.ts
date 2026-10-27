export interface ChartInteraction {
  zoom?: Array<{ startValue: number; endValue: number } | undefined>;
  selected?: Record<string, boolean>;
}

// Optional owner-scoped state; no data or chart instance is retained here.
export interface ChartInteractionStore {
  read: (key: string) => ChartInteraction | undefined;
  write: (key: string, value: ChartInteraction) => void;
}

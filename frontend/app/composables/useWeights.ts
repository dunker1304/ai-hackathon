export interface DimEntry {
  value: number;
  raw: number;
  explanation: string;
  evidence: { metric: string; value: number; source: string; fetched_at: string }[];
}

export type Dims = Record<string, DimEntry>;

export const DEFAULT_WEIGHTS: Record<string, number> = {
  demand: 0.22,
  competition: 0.2,
  growth: 0.2,
  seasonality: 0.12,
  personalization: 0.13,
  revenue: 0.13,
};

export const DIM_LABELS: Record<string, string> = {
  demand: "Demand",
  competition: "Competition",
  growth: "Growth",
  seasonality: "Seasonality",
  personalization: "Personalization",
  revenue: "Revenue",
};

/** Client-side score recompute so weight sliders update instantly. */
export function useWeights() {
  const weights = useState<Record<string, number>>("weights", () => ({ ...DEFAULT_WEIGHTS }));

  function computeTotal(dims: Dims): number {
    const keys = Object.keys(weights.value);
    const wsum = keys.reduce((acc, k) => acc + weights.value[k]!, 0) || 1;
    const total = keys.reduce((acc, k) => acc + weights.value[k]! * (dims[k]?.value ?? 0), 0);
    return Math.round((total / wsum) * 10) / 10;
  }

  /** Mirrors backend scoring.decide(): fit is a gate, not a dimension. */
  function decide(total: number, fit: number): string {
    if (fit < 50) return "not_recommend";
    if (fit < 70) return "conditional";
    if (total >= 70) return "recommend";
    return total >= 50 ? "conditional" : "not_recommend";
  }

  function reset() {
    weights.value = { ...DEFAULT_WEIGHTS };
  }

  const isDefault = computed(() =>
    Object.keys(DEFAULT_WEIGHTS).every(
      (k) => Math.abs(weights.value[k]! - DEFAULT_WEIGHTS[k]!) < 0.001,
    ),
  );

  return { weights, computeTotal, decide, reset, isDefault };
}

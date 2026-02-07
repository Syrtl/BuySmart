export interface ValueChartPoint {
  id: string;
  title: string;
  price: number;
  rating: number;
  reviewCount: number;
  quality: number;
  qualityRaw: number;
  valueScore: number;
  quality_y: number;
  intrinsic_q0: number;
  market_qm: number;
  breakdown: {
    Rn: number;
    Nn: number;
    D: number;
    S: number;
    q0_reasons?: string[];
    q0_signals?: Record<string, unknown>;
  };
}

export interface ValueChartResponse {
  productId: string;
  currency: string;
  points: ValueChartPoint[];
  optimalId: string;
  frontierIds: string[];
  explanation: string[];
}

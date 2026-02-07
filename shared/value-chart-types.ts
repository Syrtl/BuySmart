export interface ValueChartPoint {
  id: string;
  title: string;
  price: number;
  rating: number;
  reviewCount: number;
  quality: number;
  qualityRaw: number;
  valueScore: number;
}

export interface ValueChartResponse {
  productId: string;
  currency: string;
  points: ValueChartPoint[];
  optimalId: string;
  frontierIds: string[];
  explanation: string[];
}

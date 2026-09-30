// A small chart (DS-01, ADR-0012): Chart.js loaded only through a dynamic import. Typed
// stub for the red spec tests (T-DS-01-17, T-DS-01-18).
export type LazyChartType = "bar" | "line";

export interface ChartSeries {
  label: string;
  data: readonly number[];
}

export interface LazyChartProps {
  type: LazyChartType;
  /** The chart's accessible name: what it shows, in plain words. */
  label: string;
  /** The category axis, one entry per value. */
  labels: readonly string[];
  series: readonly ChartSeries[];
  /** Sizes the chart's box; the canvas fills it. */
  className?: string;
}

export function LazyChart({
  label,
  className = "relative h-48 w-full",
}: LazyChartProps) {
  return (
    <div className={className}>
      <canvas role="img" aria-label={label} />
    </div>
  );
}

// A small chart (DS-01, ADR-0012): Chart.js loaded only through a dynamic import when a
// chart first renders, so it never counts against the initial bundle (PERF-2). Only the
// bar and line parts are registered (tree-shaking; Chart.js's "auto" entry, which
// registers everything, is never imported). The colours come from the design tokens
// and follow a theme change. A page shows a chart only when it already has numbers to
// show (PRD UX: quiet by default).
import type { Chart, ChartConfiguration, ChartOptions } from "chart.js";
import { useEffect, useRef, useState } from "react";

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

/** The series colours, in order, as token names. */
const SERIES_TOKENS = [
  "--tg-accent",
  "--tg-info",
  "--tg-success",
  "--tg-warning",
] as const;

export interface ChartTheme {
  text: string;
  muted: string;
  grid: string;
  font: string;
  series: string[];
}

/** The tokens in force now (light, dark or the system's choice). */
export function chartTheme(): ChartTheme {
  const root = getComputedStyle(document.documentElement);
  const token = (name: string) => root.getPropertyValue(name).trim();
  return {
    text: token("--tg-text"),
    muted: token("--tg-text-muted"),
    grid: token("--tg-border"),
    font: getComputedStyle(document.body).fontFamily,
    series: SERIES_TOKENS.map(token),
  };
}

function seriesColour(theme: ChartTheme, index: number): string {
  return theme.series[index % theme.series.length] ?? theme.text;
}

function themedOptions(
  theme: ChartTheme,
  seriesCount: number,
): ChartOptions<LazyChartType> {
  const axis = {
    ticks: { color: theme.muted, font: { family: theme.font } },
    grid: { color: theme.grid },
    border: { color: theme.grid },
  };
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    scales: { x: axis, y: { ...axis, beginAtZero: true } },
    plugins: {
      legend: {
        display: seriesCount > 1,
        labels: { color: theme.text, font: { family: theme.font } },
      },
    },
  };
}

/** The Chart.js configuration for what the chart shows, in `theme`. */
export function chartConfig(
  { type, labels, series }: Pick<LazyChartProps, "type" | "labels" | "series">,
  theme: ChartTheme,
): ChartConfiguration<LazyChartType> {
  return {
    type,
    data: {
      labels: [...labels],
      datasets: series.map((s, index) => ({
        label: s.label,
        data: [...s.data],
        backgroundColor: seriesColour(theme, index),
        borderColor: seriesColour(theme, index),
      })),
    },
    options: themedOptions(theme, series.length),
  };
}

/** Repaints `chart` in the tokens now in force. */
function retheme(chart: Chart<LazyChartType>): void {
  const theme = chartTheme();
  chart.options = themedOptions(theme, chart.data.datasets.length);
  chart.data.datasets.forEach((dataset, index) => {
    dataset.backgroundColor = seriesColour(theme, index);
    dataset.borderColor = seriesColour(theme, index);
  });
  chart.update();
}

export function LazyChart({
  type,
  label,
  labels,
  series,
  className = "relative h-48 w-full",
}: LazyChartProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [failed, setFailed] = useState(false);
  // Rebuild the chart when what it shows changes, not on every render.
  const content = JSON.stringify({ type, labels, series });

  useEffect(() => {
    const shown = JSON.parse(content) as Pick<
      LazyChartProps,
      "type" | "labels" | "series"
    >;
    let chart: Chart<LazyChartType> | undefined;
    let cancelled = false;
    const onTheme = () => {
      if (chart) retheme(chart);
    };
    const observer = new MutationObserver(onTheme);
    const scheme =
      typeof window.matchMedia === "function"
        ? window.matchMedia("(prefers-color-scheme: dark)")
        : null;

    import("chart.js").then(
      (lib) => {
        const canvas = canvasRef.current;
        if (cancelled || canvas === null) return;
        lib.Chart.register(
          lib.BarController,
          lib.BarElement,
          lib.LineController,
          lib.LineElement,
          lib.PointElement,
          lib.CategoryScale,
          lib.LinearScale,
          lib.Legend,
          lib.Tooltip,
        );
        chart = new lib.Chart(canvas, chartConfig(shown, chartTheme()));
        // A manual theme sets data-theme on <html>; "system" follows the OS.
        observer.observe(document.documentElement, {
          attributes: true,
          attributeFilter: ["data-theme"],
        });
        scheme?.addEventListener("change", onTheme);
      },
      () => {
        if (!cancelled) setFailed(true);
      },
    );
    return () => {
      cancelled = true;
      observer.disconnect();
      scheme?.removeEventListener("change", onTheme);
      chart?.destroy();
    };
  }, [content]);

  if (failed) {
    return <p className="text-sm text-muted">The chart could not be loaded.</p>;
  }
  return (
    <div className={className}>
      <canvas ref={canvasRef} role="img" aria-label={label} />
    </div>
  );
}

// The lazy chart (DS-01, ADR-0012, PERF-2): Chart.js reaches the app only through the
// wrapper's dynamic import, so it is never in the initial bundle; the wrapper registers
// only the parts it uses, colours the chart from the design tokens, follows a theme
// change and cleans up on unmount.
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { LazyChart } from "./LazyChart";

// A stand-in for Chart.js: records what the wrapper registers and builds.
const chartMock = vi.hoisted(() => {
  interface Dataset {
    label: string;
    data: number[];
    backgroundColor: string;
    borderColor: string;
  }
  interface Config {
    type: string;
    data: { labels: string[]; datasets: Dataset[] };
    options: unknown;
  }
  const register = vi.fn();
  class Chart {
    static register = register;
    config: Config;
    options: unknown;
    data: Config["data"];
    update = vi.fn();
    destroy = vi.fn();
    constructor(_canvas: HTMLCanvasElement, config: Config) {
      this.config = config;
      this.options = config.options;
      this.data = config.data;
      instances.push(this);
    }
  }
  const instances: Chart[] = [];
  return { instances, register, Chart };
});

vi.mock("chart.js", () => ({
  Chart: chartMock.Chart,
  BarController: "BarController",
  BarElement: "BarElement",
  LineController: "LineController",
  LineElement: "LineElement",
  PointElement: "PointElement",
  CategoryScale: "CategoryScale",
  LinearScale: "LinearScale",
  Legend: "Legend",
  Tooltip: "Tooltip",
}));

const sources = import.meta.glob<string>(
  [
    "../../**/*.{ts,tsx}",
    "!../../**/*.test.{ts,tsx}",
    "!../../api/**",
    "!../../test/**",
  ],
  { query: "?raw", import: "default", eager: true },
);

beforeEach(() => {
  chartMock.instances.length = 0;
  chartMock.register.mockClear();
  document.documentElement.style.setProperty("--tg-accent", "#5d47de");
});

afterEach(() => {
  document.documentElement.style.removeProperty("--tg-accent");
  document.documentElement.removeAttribute("data-theme");
});

test("[DS-01][PERF-2] T-DS-01-17 only the chart wrapper loads chart.js, and only through a dynamic import", () => {
  expect(Object.keys(sources).length).toBeGreaterThan(50);
  const loaders = Object.entries(sources)
    .filter(([, text]) =>
      /(?:from\s+|import\s*\(\s*|require\s*\(\s*)["']chart\.js/.test(
        text.replace(/import\s+type\s[^;]*;/g, ""),
      ),
    )
    .map(([path]) => path);
  expect(loaders).toEqual(["./LazyChart.tsx"]);
  const wrapper = sources["./LazyChart.tsx"] ?? "";
  const valueImports = wrapper
    .replace(/import\s+type\s[^;]*;/g, "")
    .match(/from\s+["']chart\.js[^"']*["']/g);
  expect(valueImports).toBeNull();
  expect(wrapper).toMatch(/import\(\s*["']chart\.js["']\s*\)/);
  expect(wrapper).not.toMatch(/chart\.js\/auto/);
});

test("[DS-01][PERF-2] T-DS-01-18 the chart loads on render, is themed from the tokens, repaints on a theme change and cleans up", async () => {
  const { unmount } = render(
    <LazyChart
      type="bar"
      label="Tasks done per day"
      labels={["Mon", "Tue"]}
      series={[{ label: "Done", data: [3, 5] }]}
    />,
  );
  expect(
    screen.getByRole("img", { name: "Tasks done per day" }),
  ).toBeInTheDocument();
  await waitFor(() => {
    expect(chartMock.instances).toHaveLength(1);
  });
  expect(chartMock.register).toHaveBeenCalledWith(
    "BarController",
    "BarElement",
    "LineController",
    "LineElement",
    "PointElement",
    "CategoryScale",
    "LinearScale",
    "Legend",
    "Tooltip",
  );
  const [chart] = chartMock.instances;
  expect(chart?.config.type).toBe("bar");
  expect(chart?.config.data.labels).toEqual(["Mon", "Tue"]);
  expect(chart?.config.data.datasets[0]?.data).toEqual([3, 5]);
  expect(chart?.config.data.datasets[0]?.backgroundColor).toBe("#5d47de");

  document.documentElement.style.setProperty("--tg-accent", "#9c8cff");
  document.documentElement.dataset.theme = "dark";
  await waitFor(() => {
    expect(chart?.update).toHaveBeenCalled();
  });
  expect(chart?.data.datasets[0]?.backgroundColor).toBe("#9c8cff");

  unmount();
  expect(chart?.destroy).toHaveBeenCalledTimes(1);
});

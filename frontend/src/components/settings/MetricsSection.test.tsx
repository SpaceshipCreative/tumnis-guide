// Settings > Metrics (P1-18, PRD Success metrics): the PRD's success metrics computed on the
// server from what Postgres already holds, each next to its target; agent metrics wait for
// phase 2 and unattended runs for phase 4. The phase 1 exit gate (working days planned in a
// row, the two-week dogfood clock) shows first. Nothing is sent anywhere but `/v1`.
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { METRICS, metricsSummary } from "../../test/msw/closeDay";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { MetricsSection } from "./MetricsSection";

function row(name: string): HTMLElement {
  const label = screen.getByText(name, {
    selector: "dt, th, [role=rowheader]",
  });
  const container = label.closest("[data-metric]");
  if (!(container instanceof HTMLElement))
    throw new Error(`no row for ${name}`);
  return container;
}

describe("MetricsSection", () => {
  test.fails(
    "[P1-18][Success metrics] T-P1-18-10 shows metrics with targets and no external requests",
    async () => {
      const asked: URLSearchParams[] = [];
      server.use(metricsSummary(METRICS, (query) => asked.push(query)));
      const urls: URL[] = [];
      const record = ({ request }: { request: Request }) => {
        urls.push(new URL(request.url));
      };
      server.events.on("request:start", record);
      try {
        renderWithProviders(<MetricsSection />);

        // The exit gate first: 3 of the 10 working days planned in a row.
        const gate = await screen.findByRole("region", {
          name: "Planned days in a row",
        });
        expect(gate).toHaveTextContent("3");
        expect(gate).toHaveTextContent("of 10 working days");

        // Values next to their PRD targets; no data yet reads as such, never as 0.
        expect(row("Daily open rate")).toHaveTextContent("86%");
        expect(row("Daily open rate")).toHaveTextContent("5 of 7 days");
        expect(row("Tasks completed per working day")).toHaveTextContent("2.5");
        expect(row("Rollover rate")).toHaveTextContent("10%");
        expect(row("Rollover rate")).toHaveTextContent(
          "under 20% of planned tasks",
        );
        expect(row("Estimate error")).toHaveTextContent("No data yet");
        expect(row("Estimate error")).not.toHaveTextContent("0%");

        // Agent metrics wait for phase 2, unattended runs for phase 4.
        expect(row("Share done by agents")).toHaveTextContent(
          "Available after phase 2",
        );
        expect(row("Agent result acceptance")).toHaveTextContent(
          "Available after phase 2",
        );
        expect(row("Unattended overnight runs")).toHaveTextContent(
          "Available after phase 4",
        );
        expect(
          within(row("Share done by agents")).queryByText("No data yet"),
        ).not.toBeInTheDocument();
      } finally {
        server.events.removeListener("request:start", record);
      }

      // One summary read over the last two weeks; every request went to this app's /v1
      // (setup.ts fails the test on any request without a handler).
      expect(asked).toHaveLength(1);
      expect(asked[0]?.get("from")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(asked[0]?.get("to")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(urls.length).toBeGreaterThan(0);
      for (const url of urls) {
        expect(url.origin).toBe(window.location.origin);
        expect(url.pathname.startsWith("/v1/")).toBe(true);
      }
    },
  );
});

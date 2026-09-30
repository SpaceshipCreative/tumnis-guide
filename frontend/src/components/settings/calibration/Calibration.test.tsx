// Settings > Calibration (P3-08, FR-11.5): per decision point, accuracy once 100 labeled
// outcomes exist (until then, how many more are needed), and a threshold editor that
// needs a reason. Nothing is suggested: the human decides.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { server } from "../../../test/msw/server";
import { Recorder } from "../../../test/msw/settings";
import { renderWithProviders } from "../../../test/render";

interface CalibrationModule {
  CalibrationSection: () => React.JSX.Element;
}

// Loaded at run time, so this file compiles before Calibration.tsx exists.
async function load<T>(path: string): Promise<T> {
  return (await import(/* @vite-ignore */ path)) as T;
}

const METRICS = {
  n: 120,
  auto_rate: 80 / 120,
  auto_precision: 0.9,
  review_rate: 40 / 120,
  overall_accuracy: 92 / 120,
};

function evaluation(labeled: number, withMetrics: boolean) {
  return {
    decision_point: "project_match",
    provider: "jev",
    model_version: "jev-1.13.0",
    labeled,
    needed: Math.max(0, 100 - labeled),
    threshold: 0.85,
    metrics: withMetrics ? METRICS : null,
    explicit_metrics: null,
    sweep: withMetrics
      ? [
          { threshold: 0.8, ...METRICS },
          { threshold: 0.9, ...METRICS, auto_rate: 50 / 120 },
        ]
      : [],
  };
}

function page(labeled: number, withMetrics: boolean) {
  return {
    model_version: "jev-1.13.0",
    min_labeled: 100,
    points: [
      {
        decision_point: "project_match",
        primitive: "choice",
        threshold: {
          min_confidence: 0.85,
          t_yes: null,
          t_no: null,
          fallback_margin: 0.1,
        },
        source: "default",
        needs_recheck: false,
        bar: 0.85,
        evaluations: [evaluation(labeled, withMetrics)],
      },
      {
        decision_point: "actionability",
        primitive: "noul",
        threshold: {
          min_confidence: null,
          t_yes: 0.85,
          t_no: 0.15,
          fallback_margin: 0.1,
        },
        source: "default",
        needs_recheck: true,
        bar: 0.7,
        evaluations: [],
      },
    ],
  };
}

function handlers(recorder: Recorder, loaded: ReturnType<typeof page>) {
  return [
    http.get("*/v1/decisions/calibration", () => HttpResponse.json(loaded)),
    http.put("*/v1/decisions/thresholds/:point", async ({ request }) => {
      await recorder.record(request);
      const [first] = loaded.points;
      return HttpResponse.json({
        ...first,
        threshold: { ...first?.threshold, min_confidence: 0.8 },
        source: "user",
      });
    }),
  ];
}

test.fails("[P3-08][FR-11.5] shows not enough data then metrics", async () => {
  const { CalibrationSection } = await load<CalibrationModule>("./Calibration");

  // Under 100 labeled outcomes: no numbers, just how many more are needed.
  const quiet = new Recorder();
  server.use(...handlers(quiet, page(40, false)));
  const first = renderWithProviders(<CalibrationSection />);
  const matching = await screen.findByRole("region", {
    name: "Project match",
  });
  expect(within(matching).getByText(/60 more needed/)).toBeInTheDocument();
  expect(within(matching).queryByRole("table")).not.toBeInTheDocument();
  const actionable = screen.getByRole("region", { name: "Actionability" });
  expect(within(actionable).getByText(/model changed/i)).toBeInTheDocument();
  first.unmount();

  // At 100 or more: the table, and an editor that needs a reason.
  const recorder = new Recorder();
  server.use(...handlers(recorder, page(120, true)));
  const { user } = renderWithProviders(<CalibrationSection />);
  const card = await screen.findByRole("region", { name: "Project match" });
  const table = await within(card).findByRole("table");
  expect(within(table).getByText("67%")).toBeInTheDocument(); // auto-applied
  expect(within(table).getByText("90%")).toBeInTheDocument(); // right when auto-applied

  await user.click(
    within(card).getByRole("button", { name: /change threshold/i }),
  );
  const value = within(card).getByLabelText("Minimum confidence");
  await user.clear(value);
  await user.type(value, "0.8");
  await user.click(within(card).getByRole("button", { name: "Save" }));
  expect(await within(card).findByRole("alert")).toHaveTextContent(/reason/i);
  expect(recorder.writes()).toEqual([]);

  await user.type(
    within(card).getByLabelText("Reason"),
    "Right 97 times in 100 at 0.80",
  );
  await user.click(within(card).getByRole("button", { name: "Save" }));
  await waitFor(() => {
    expect(recorder.writes()).toEqual([
      "PUT /v1/decisions/thresholds/project_match",
    ]);
  });
  expect(recorder.sent[0]?.body).toEqual({
    threshold: { min_confidence: 0.8 },
    reason: "Right 97 times in 100 at 0.80",
  });
});

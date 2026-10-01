// The run view shows the profile version the run started with (P2-12, Quality rule 3):
// `runs.profile_version` from the daemon's `status{started}`, so a profile change can be
// traced to the runs it made. A run without one shows nothing in its place.
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { RunView } from "./RunView";

const RUN_ID = "01950000-0000-7000-8000-000000000d01";
const STARTED = "2026-03-09T12:00:00Z";

function useRun(profileVersion: string | null) {
  server.use(
    http.get("*/v1/runs/:id", () =>
      HttpResponse.json({
        id: RUN_ID,
        task_id: null,
        project_id: null,
        kind: "task",
        status: "succeeded",
        stop_reason: null,
        rerun_of: null,
        started_at: STARTED,
        finished_at: "2026-03-09T12:02:00Z",
        created_at: STARTED,
        profile_version: profileVersion,
      }),
    ),
    http.get("*/v1/runs/:id/events", () =>
      HttpResponse.json({ items: [], next_after_seq: null }),
    ),
  );
}

test("[P2-12][Quality rule 3] shows the run's profile version", async () => {
  useRun("1.1.0");
  renderWithProviders(<RunView runId={RUN_ID} />);
  expect(await screen.findByTestId("run-profile-version")).toHaveTextContent(
    "Profile 1.1.0",
  );
});

test("[P2-12][Quality rule 3] a run without a profile version shows none", async () => {
  useRun(null);
  renderWithProviders(<RunView runId={RUN_ID} />);
  expect(await screen.findByText("Finished")).toBeInTheDocument();
  expect(screen.queryByTestId("run-profile-version")).toBeNull();
});

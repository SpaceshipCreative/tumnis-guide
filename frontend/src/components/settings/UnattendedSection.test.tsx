// Settings > Unattended runs (P4-04, FR-4.5): the workspace's window, its nights and its
// local start and end (an end before the start runs overnight), saved in one PUT with the
// version it read, and turned off with `window: null`.
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { zUnattendedWindowIn } from "../../api/zod.gen";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { unattendedWindowHandlers } from "../../test/msw/unattended";
import { renderWithProviders } from "../../test/render";
import { UnattendedSection } from "./UnattendedSection";

describe("UnattendedSection", () => {
  test("[P4-04][FR-4.5] set the workspace window, then turn it off", async () => {
    const recorder = new Recorder();
    server.use(...unattendedWindowHandlers(recorder));
    const { user } = renderWithProviders(<UnattendedSection />);

    expect(
      await screen.findByText("No window: nothing runs unattended."),
    ).toBeInTheDocument();
    // A first window offers weeknights, 22:00 to 06:00, and says it runs overnight.
    for (const day of [
      "Monday",
      "Tuesday",
      "Wednesday",
      "Thursday",
      "Friday",
    ]) {
      expect(screen.getByRole("checkbox", { name: day })).toBeChecked();
    }
    expect(
      screen.getByRole("checkbox", { name: "Saturday" }),
    ).not.toBeChecked();
    expect(screen.getByLabelText("Starts at")).toHaveValue("22:00");
    expect(screen.getByLabelText("Ends at")).toHaveValue("06:00");
    expect(
      screen.getByText(/An end before the start runs overnight/),
    ).toHaveTextContent("workspace timezone");
    // Nothing stored yet: nothing to turn off.
    expect(screen.queryByRole("button", { name: "Turn off" })).toBeNull();

    await user.click(screen.getByRole("checkbox", { name: "Saturday" }));
    fireEvent.change(screen.getByLabelText("Starts at"), {
      target: { value: "23:00" },
    });
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(recorder.writes()).toEqual(["PUT /v1/unattended/window"]);
    });
    const [put] = recorder.sent;
    expect(put?.idempotencyKey).toBeTruthy();
    expect(zUnattendedWindowIn.safeParse(put?.body).success).toBe(true);
    expect(put?.body).toEqual({
      project_id: null,
      window: {
        weekdays: [0, 1, 2, 3, 4, 5],
        start_local: "23:00:00",
        end_local: "06:00:00",
      },
      version: null,
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
    expect(
      screen.getByText(
        "Runs Mon, Tue, Wed, Thu, Fri, Sat, 23:00 to 06:00 (overnight).",
      ),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Turn off" }));
    await waitFor(() => {
      expect(recorder.sent).toHaveLength(2);
    });
    expect(recorder.sent[1]?.body).toEqual({
      project_id: null,
      window: null,
      version: 1,
    });
    expect(await screen.findByText("Turned off.")).toBeInTheDocument();
    expect(
      screen.getByText("No window: nothing runs unattended."),
    ).toBeInTheDocument();
  });

  test("[P4-04][FR-4.5] a window with no nights or no length is not sent", async () => {
    const recorder = new Recorder();
    server.use(...unattendedWindowHandlers(recorder));
    const { user } = renderWithProviders(<UnattendedSection />);

    const end = await screen.findByLabelText("Ends at");
    fireEvent.change(end, { target: { value: "22:00" } });
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The start and the end must differ.",
    );

    fireEvent.change(end, { target: { value: "06:00" } });
    for (const day of [
      "Monday",
      "Tuesday",
      "Wednesday",
      "Thursday",
      "Friday",
    ]) {
      await user.click(screen.getByRole("checkbox", { name: day }));
    }
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Pick at least one day.",
    );
    expect(recorder.sent).toEqual([]);
  });
});

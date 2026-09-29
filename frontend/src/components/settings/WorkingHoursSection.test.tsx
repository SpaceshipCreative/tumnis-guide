// Settings > Working hours (P1-10, FR-4.7): Monday to Friday start and end in the
// workspace timezone. The form validates with the generated zod schema plus one rule (the
// end comes after the start) and saves the week in one PUT with the version it read.
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { zWorkingHoursIn } from "../../api/zod.gen";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { workingHoursHandlers } from "../../test/msw/planning";
import { renderWithProviders } from "../../test/render";
import { WorkingHoursSection } from "./WorkingHoursSection";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];

describe("WorkingHoursSection", () => {
  test("[P1-10][FR-4.7] T-P1-10-13 edit weekday hours", async () => {
    const recorder = new Recorder();
    server.use(...workingHoursHandlers(recorder));
    const { user } = renderWithProviders(<WorkingHoursSection />);

    const mondayEnd = await screen.findByLabelText("Monday end");
    for (const day of WEEKDAYS) {
      expect(screen.getByLabelText(`${day} start`)).toHaveValue("09:00");
      expect(screen.getByLabelText(`${day} end`)).toHaveValue("18:00");
    }
    expect(screen.queryByLabelText("Saturday start")).not.toBeInTheDocument();

    // An end before the start is refused in the browser; nothing is sent.
    fireEvent.change(mondayEnd, { target: { value: "08:00" } });
    await user.click(screen.getByRole("button", { name: "Save" }));
    const message = "End must be after start";
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(mondayEnd).toHaveAccessibleDescription(message);
    expect(recorder.sent).toEqual([]);

    // Fixed, the week is saved in one PUT the generated schema accepts.
    fireEvent.change(mondayEnd, { target: { value: "17:30" } });
    fireEvent.change(screen.getByLabelText("Friday start"), {
      target: { value: "10:00" },
    });
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(recorder.writes()).toEqual(["PUT /v1/settings/working-hours"]);
    });
    const [put] = recorder.sent;
    expect(put?.idempotencyKey).toBeTruthy();
    expect(zWorkingHoursIn.safeParse(put?.body).success).toBe(true);
    expect(put?.body).toEqual({
      days: [
        { weekday: 0, start: "09:00", end: "17:30" },
        { weekday: 1, start: "09:00", end: "18:00" },
        { weekday: 2, start: "09:00", end: "18:00" },
        { weekday: 3, start: "09:00", end: "18:00" },
        { weekday: 4, start: "10:00", end: "18:00" },
      ],
      version: 2,
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
  });
});

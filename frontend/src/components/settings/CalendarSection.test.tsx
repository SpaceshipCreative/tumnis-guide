import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import {
  CALENDAR_ACCOUNTS,
  calendarHandlers,
  CONSENT_URL,
} from "../../test/msw/calendar";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderWithProviders } from "../../test/render";
import { CalendarSection } from "./CalendarSection";

describe("CalendarSection", () => {
  test.fails(
    "[P1-09][FR-1.3] T-P1-09-12 connect and choose calendars",
    async () => {
      const recorder = new Recorder();
      server.use(...calendarHandlers(recorder));
      const opened: string[] = [];
      const { user } = renderWithProviders(
        <CalendarSection
          openUrl={(url) => {
            opened.push(url);
          }}
        />,
      );

      const [avery, blake] = CALENDAR_ACCOUNTS;
      if (!avery || !blake) throw new Error("two accounts");
      const averyCard = await screen.findByRole("group", {
        name: avery.google_email,
      });
      const blakeCard = screen.getByRole("group", { name: blake.google_email });

      // Status: the last sync for a connected account, `Needs reauth` for a revoked one.
      expect(within(averyCard).getByText(/Last synced/)).toBeInTheDocument();
      expect(within(blakeCard).getByText("Needs reauth")).toBeInTheDocument();
      expect(
        within(blakeCard).getByRole("button", { name: "Reconnect" }),
      ).toBeInTheDocument();

      // Calendars can be toggled: one PUT with the new selection and the version read.
      const primary = within(averyCard).getByRole("checkbox", {
        name: "Avery",
      });
      const team = within(averyCard).getByRole("checkbox", {
        name: "Team calendar",
      });
      expect(primary).toBeChecked();
      expect(team).not.toBeChecked();
      await user.click(team);
      await waitFor(() => {
        expect(recorder.writes()).toEqual([
          `PUT /v1/calendar/accounts/${avery.id}/calendars`,
        ]);
      });
      expect(recorder.sent[0]?.body).toEqual({
        selected_calendar_ids: ["avery@example.com", "team@group.example.com"],
        version: avery.version,
      });
      expect(recorder.sent[0]?.idempotencyKey).toBeTruthy();

      // Connect opens Google's consent page.
      await user.click(
        screen.getByRole("button", { name: "Connect Google account" }),
      );
      await waitFor(() => {
        expect(opened).toEqual([CONSENT_URL]);
      });
    },
  );
});

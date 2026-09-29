import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { server } from "../../test/msw/server";
import { Recorder, sessionHandlers, sessionRow } from "../../test/msw/settings";
import { renderWithProviders } from "../../test/render";
import { SessionsSection } from "./SessionsSection";

describe("SessionsSection", () => {
  test.fails(
    "[P0-26][SEC-1] T-P0-26-08 sign out other devices keeps this one",
    async () => {
      const recorder = new Recorder();
      server.use(...sessionHandlers(recorder));
      const { user } = renderWithProviders(<SessionsSection />);

      const items = await screen.findAllByRole("listitem");
      expect(items).toHaveLength(3);
      const [mine] = items;
      if (!mine) throw new Error("three items");
      expect(within(mine).getByText("This device")).toBeInTheDocument();

      await user.click(
        screen.getByRole("button", { name: "Sign out other devices" }),
      );
      const dialog = await screen.findByRole("dialog", {
        name: "Sign out every other device?",
      });
      await user.click(
        within(dialog).getByRole("button", { name: "Sign out" }),
      );

      await waitFor(() => {
        expect(screen.getAllByRole("listitem")).toHaveLength(1);
      });
      expect(recorder.sent).toHaveLength(1);
      expect(recorder.sent[0]?.method).toBe("DELETE");
      expect(recorder.sent[0]?.path).toBe("/v1/auth/sessions");
      expect(recorder.sent[0]?.search).toBe("");
      expect(recorder.sent[0]?.idempotencyKey).toBeTruthy();
      const remaining = screen.getByRole("listitem");
      expect(within(remaining).getByText("This device")).toBeInTheDocument();
      expect(
        within(remaining).getByText(String(sessionRow(1).user_agent)),
      ).toBeInTheDocument();
    },
  );
});

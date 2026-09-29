import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { server } from "../../test/msw/server";
import {
  accountHandlers,
  NEW_TOTP_SECRET,
  Recorder,
} from "../../test/msw/settings";
import { renderWithProviders } from "../../test/render";
import { AccountSection } from "./AccountSection";

describe("AccountSection", () => {
  test("[P0-26][SEC-1] T-P0-26-09 shows second-factor status and starts re-enrolment", async () => {
    const recorder = new Recorder();
    server.use(...accountHandlers(recorder));
    const { user, queryClient } = renderWithProviders(<AccountSection />);

    expect(await screen.findByText("scott@example.com")).toBeInTheDocument();
    const status = screen.getByText(/Authenticator app \(TOTP\): on since/);
    expect(status).toHaveTextContent(
      new Date("2026-03-01T09:00:00Z").toLocaleDateString(),
    );

    await user.click(
      screen.getByRole("button", { name: "Set up a new authenticator" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Set up a new authenticator",
    });
    await user.type(
      within(dialog).getByLabelText("Current password"),
      "correct horse battery",
    );
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));

    expect(
      await within(dialog).findByText(NEW_TOTP_SECRET),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("link", { name: "Open in authenticator app" }),
    ).toHaveAttribute("href", expect.stringMatching(/^otpauth:\/\/totp\//));
    await user.type(
      within(dialog).getByLabelText("Code from the new app"),
      "123456",
    );
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(await screen.findByRole("status")).toHaveTextContent(
      /new authenticator is set up/i,
    );
    expect(recorder.writes()).toEqual([
      "POST /v1/auth/totp/enrol",
      "POST /v1/auth/totp/enrol/confirm",
    ]);
    expect(recorder.sent[0]?.body).toEqual({
      password: "correct horse battery",
    });
    expect(recorder.sent[1]?.body).toEqual({
      enrol_token: "enrol-token-1",
      code: "123456",
    });
    await waitFor(() => {
      expect(
        screen.getByText(/Authenticator app \(TOTP\): on since/),
      ).toHaveTextContent(
        new Date("2026-03-09T12:00:00Z").toLocaleDateString(),
      );
    });
    const cached = JSON.stringify([
      queryClient
        .getQueryCache()
        .getAll()
        .map((q) => q.state.data),
      queryClient
        .getMutationCache()
        .getAll()
        .map((m) => m.state.data),
    ]);
    expect(cached).not.toContain(NEW_TOTP_SECRET);
    expect(document.body.textContent).not.toContain(NEW_TOTP_SECRET);
  });
});

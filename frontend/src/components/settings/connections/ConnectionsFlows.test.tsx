import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import {
  AUTHORIZE_URL,
  connectionRow,
  ConnectionsFake,
} from "../../../test/msw/connections";
import { server } from "../../../test/msw/server";
import { Recorder } from "../../../test/msw/settings";
import { renderWithProviders } from "../../../test/render";
import { Connections } from "./Connections";
import { lastSyncedText, relativeTime } from "./status";

const WORK = connectionRow();
const PERSONAL = connectionRow({
  id: "01890000-0000-7000-8000-0000000003b2",
  account_label: "Personal mail",
  status: "auth_required",
  status_detail: "Sign in again",
  last_success_at: null,
  version: 4,
});

function setup(rows = [WORK, PERSONAL]) {
  const recorder = new Recorder();
  const fake = new ConnectionsFake(rows);
  server.use(...fake.handlers(recorder));
  const opened: string[] = [];
  const view = renderWithProviders(
    <Connections
      pollMs={1}
      openUrl={(url) => {
        opened.push(url);
      }}
    />,
    { viewport: "phone" },
  );
  return { ...view, recorder, fake, opened };
}

describe("Connections flows", () => {
  test("[P3-02][FR-14.4] the wizard needs the consent, creates, starts the sign-in and opens it", async () => {
    const { user, recorder, opened } = setup();
    await screen.findByRole("group", { name: "Work mail" });

    await user.click(screen.getByRole("button", { name: "Connect account" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Connect an account",
    });
    await within(dialog).findByRole("combobox", { name: "Service" });
    expect(
      within(dialog).getByText(/shares your messages with Tumnis/),
    ).toBeInTheDocument();
    await user.type(
      within(dialog).getByRole("textbox", { name: "Account name" }),
      "Team mail",
    );
    const connect = within(dialog).getByRole("button", { name: "Connect" });
    expect(connect).toBeDisabled();

    await user.click(
      within(dialog).getByRole("checkbox", { name: "I understand and agree" }),
    );
    expect(connect).toBeEnabled();
    await user.click(connect);

    await waitFor(() => {
      expect(opened).toEqual([AUTHORIZE_URL]);
    });
    expect(recorder.writes()).toEqual([
      "POST /v1/connections",
      "POST /v1/connections/01890000-0000-7000-8000-0000000003bf/oauth/start",
    ]);
    const create = recorder.sent.find((s) => s.method === "POST");
    expect(create?.body).toEqual({
      provider: "fake",
      account_label: "Team mail",
      consent_acknowledged: true,
      settings: { backfill_days: 30 },
    });
    expect(create?.idempotencyKey).toBeTruthy();
    // The URL was polled until the server had it (null first).
    expect(
      recorder.sent.filter((s) => s.path.endsWith("/oauth/url")),
    ).toHaveLength(2);
  });

  test("[P3-02][FR-14.4] a provider without sign-in connects at once", async () => {
    const { user, recorder, opened } = setup();
    await screen.findByRole("group", { name: "Work mail" });
    await user.click(screen.getByRole("button", { name: "Connect account" }));
    const dialog = await screen.findByRole("dialog");
    await user.selectOptions(
      await within(dialog).findByRole("combobox", { name: "Service" }),
      "fake_open",
    );
    expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
    await user.type(
      within(dialog).getByRole("textbox", { name: "Account name" }),
      "Team notes",
    );
    await user.click(within(dialog).getByRole("button", { name: "Connect" }));

    expect(
      await screen.findByRole("group", { name: "Team notes" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Connected Team notes.",
    );
    expect(recorder.writes()).toEqual(["POST /v1/connections"]);
    expect(opened).toEqual([]);
  });

  test("[P3-02][FR-14.4] Reconnect starts a new sign-in; Sync now posts a sync", async () => {
    const { user, recorder, opened } = setup();
    const personal = await screen.findByRole("group", {
      name: "Personal mail",
    });
    await user.click(
      within(personal).getByRole("button", { name: "Reconnect" }),
    );
    await waitFor(() => {
      expect(opened).toEqual([AUTHORIZE_URL]);
    });

    const work = screen.getByRole("group", { name: "Work mail" });
    await user.click(within(work).getByRole("button", { name: "Sync now" }));
    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(
        "Syncing Work mail.",
      );
    });
    expect(recorder.writes()).toEqual([
      `POST /v1/connections/${PERSONAL.id}/oauth/start`,
      `POST /v1/connections/${WORK.id}/sync`,
    ]);
  });

  test("[P3-02][FR-14.4] details save settings at the version read, keeping the rest", async () => {
    const { user, recorder } = setup();
    const work = await screen.findByRole("group", { name: "Work mail" });
    await user.click(within(work).getByRole("button", { name: "Details" }));

    const backfill = within(work).getByRole("spinbutton", {
      name: "Days to look back",
    });
    await user.clear(backfill);
    await user.type(backfill, "60");
    await user.type(
      within(work).getByRole("spinbutton", { name: "Sync every (minutes)" }),
      "15",
    );
    await user.click(
      within(work).getByRole("button", { name: "Save settings" }),
    );

    await waitFor(() => {
      expect(recorder.writes()).toEqual([`PATCH /v1/connections/${WORK.id}`]);
    });
    expect(recorder.sent[0]?.body).toEqual({
      account_label: "Work mail",
      settings: {
        backfill_days: 60,
        sync_every_min: 15,
        allowlist: [],
        extra: {},
      },
      version: WORK.version,
    });
    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("Saved Work mail.");
    });
  });

  test("[P3-02][FR-14.4] Disconnect asks for a reason and sends it", async () => {
    const { user, recorder } = setup();
    const work = await screen.findByRole("group", { name: "Work mail" });
    await user.click(within(work).getByRole("button", { name: "Details" }));
    await user.click(within(work).getByRole("button", { name: "Disconnect" }));

    const dialog = screen.getByRole("dialog", {
      name: "Disconnect Work mail?",
    });
    const confirm = within(dialog).getByRole("button", { name: "Disconnect" });
    expect(confirm).toBeDisabled();
    await user.type(
      within(dialog).getByRole("textbox", { name: "Reason" }),
      "Switched providers",
    );
    await user.click(confirm);

    await waitFor(() => {
      expect(
        screen.queryByRole("group", { name: "Work mail" }),
      ).not.toBeInTheDocument();
    });
    expect(recorder.writes()).toEqual([`DELETE /v1/connections/${WORK.id}`]);
    expect(recorder.sent[0]?.body).toEqual({ reason: "Switched providers" });
  });
});

describe("connection status text", () => {
  const now = new Date("2026-03-09T12:00:00Z");

  test("[P3-02][FR-14.4] last sync in words, or never", () => {
    expect(lastSyncedText(null, now)).toBe("Never synced");
    expect(lastSyncedText("2026-03-09T11:55:00Z", now)).toMatch(
      /^Last synced 5 minutes ago$/,
    );
    expect(relativeTime(new Date("2026-03-09T11:59:40Z"), now)).toBe(
      "just now",
    );
  });
});

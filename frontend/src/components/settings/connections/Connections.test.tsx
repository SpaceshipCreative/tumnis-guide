import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";

import { server } from "../../../test/msw/server";
import { renderWithProviders } from "../../../test/render";

// Three connected accounts as `GET /v1/connections` lists them (P3-02): one healthy, one
// whose provider keeps failing (retrying with backoff), one whose sign-in expired.
const CONNECTIONS = [
  {
    id: "01890000-0000-7000-8000-0000000003a1",
    kind: "email",
    provider: "fake",
    account_label: "Work mail",
    status: "ok",
    status_detail: null,
    last_success_at: "2026-03-09T11:55:00Z",
    next_sync_at: "2026-03-09T12:00:00Z",
    settings: {
      backfill_days: 30,
      sync_every_min: null,
      allowlist: [],
      extra: {},
    },
    version: 2,
  },
  {
    id: "01890000-0000-7000-8000-0000000003a2",
    kind: "notes",
    provider: "fake",
    account_label: "Meeting notes",
    status: "degraded",
    status_detail: "Server error from provider, retrying",
    last_success_at: "2026-03-09T10:00:00Z",
    next_sync_at: "2026-03-09T12:20:00Z",
    settings: {
      backfill_days: 30,
      sync_every_min: null,
      allowlist: [],
      extra: {},
    },
    version: 5,
  },
  {
    id: "01890000-0000-7000-8000-0000000003a3",
    kind: "email",
    provider: "fake",
    account_label: "Personal mail",
    status: "auth_required",
    status_detail: "Sign in again",
    last_success_at: null,
    next_sync_at: null,
    settings: {
      backfill_days: 30,
      sync_every_min: null,
      allowlist: [],
      extra: {},
    },
    version: 4,
  },
];

describe("Connections", () => {
  test("[P3-02][FR-14.4] T-P3-02-14 shows status, last sync and reconnect", async () => {
    server.use(
      http.get("*/v1/connections", () => HttpResponse.json(CONNECTIONS)),
    );
    // Imported here, so this file loads before the screen exists (the spec is red).
    const screenModule = "./Connections";
    const { Connections } = (await import(/* @vite-ignore */ screenModule)) as {
      Connections: () => React.JSX.Element;
    };
    renderWithProviders(<Connections />, { viewport: "phone" });

    const work = await screen.findByRole("group", { name: "Work mail" });
    const notes = screen.getByRole("group", { name: "Meeting notes" });
    const personal = screen.getByRole("group", { name: "Personal mail" });

    // A status chip on every account, with what went wrong when it is not healthy.
    expect(within(work).getByText("Connected")).toBeInTheDocument();
    expect(within(notes).getByText("Retrying")).toBeInTheDocument();
    expect(
      within(notes).getByText("Server error from provider, retrying"),
    ).toBeInTheDocument();
    expect(within(personal).getByText("Sign in needed")).toBeInTheDocument();
    expect(within(personal).getByText("Sign in again")).toBeInTheDocument();

    // When it last synced, or that it never has.
    expect(within(work).getByText(/Last synced/)).toBeInTheDocument();
    expect(within(personal).getByText(/Never synced/)).toBeInTheDocument();

    // Reconnect only where the sign-in expired; Sync now everywhere else.
    expect(
      within(personal).getByRole("button", { name: "Reconnect" }),
    ).toBeInTheDocument();
    expect(
      within(work).queryByRole("button", { name: "Reconnect" }),
    ).not.toBeInTheDocument();
    expect(
      within(work).getByRole("button", { name: "Sync now" }),
    ).toBeInTheDocument();
  });
});

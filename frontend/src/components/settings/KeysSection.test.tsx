import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test, vi } from "vitest";

import type { KeyOut } from "../../api/types.gen";
import { server } from "../../test/msw/server";
import { KEY_ID, keyRow, Recorder } from "../../test/msw/settings";
import { renderWithProviders } from "../../test/render";
import { KeysSection } from "./KeysSection";

const SECRET = "tmn_abcdefghijkl_0123456789abcdefghijklmnopqrstuvwxyzABCDEFG";
const ROTATED = "tmn_mnopqrstuvwx_ZYXWVUTSRQPONMLKJIHGFEDCBA9876543210zyxwvut";
const LAST_USED = "2026-03-09T11:58:00Z";

describe("KeysSection", () => {
  test("[P0-26][FR-9.3] T-P0-26-04 a new key is shown once and never cached", async () => {
    const recorder = new Recorder();
    let items: KeyOut[] = [];
    server.use(
      http.get("*/v1/keys", () =>
        HttpResponse.json({ items, next_cursor: null }),
      ),
      http.post("*/v1/keys", async ({ request }) => {
        await recorder.record(request);
        items = [keyRow()];
        return HttpResponse.json({ ...keyRow(), key: SECRET }, { status: 201 });
      }),
    );
    const { user, queryClient } = renderWithProviders(<KeysSection />);
    const writeText = vi.spyOn(navigator.clipboard, "writeText");

    await user.type(await screen.findByLabelText("Key name"), "ci");
    await user.click(screen.getByRole("checkbox", { name: "tasks:read" }));
    await user.click(screen.getByRole("button", { name: "Create key" }));

    const dialog = await screen.findByRole("dialog", {
      name: "Copy your new key",
    });
    expect(within(dialog).getByText(SECRET)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith(SECRET);
    expect(recorder.writes()).toEqual(["POST /v1/keys"]);

    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    const row = await screen.findByRole("row", { name: /laptop script/ });
    expect(within(row).getByText("abcdefghijkl")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(SECRET);
    const cached = JSON.stringify(
      queryClient
        .getQueryCache()
        .getAll()
        .map((query) => query.state.data),
    );
    expect(cached).not.toContain(SECRET);
    const mutations = JSON.stringify(
      queryClient
        .getMutationCache()
        .getAll()
        .map((mutation) => mutation.state.data),
    );
    expect(mutations).not.toContain(SECRET);
  });

  test("[P0-26][FR-9.3] T-P0-26-05 rotate and revoke call the right endpoints and last use shows", async () => {
    const recorder = new Recorder();
    server.use(
      http.get("*/v1/keys", () =>
        HttpResponse.json({
          items: [keyRow({ last_used_at: LAST_USED })],
          next_cursor: null,
        }),
      ),
      http.post("*/v1/keys/:id/rotate", async ({ request }) => {
        await recorder.record(request);
        return HttpResponse.json({
          ...keyRow({ prefix: "mnopqrstuvwx" }),
          key: ROTATED,
        });
      }),
      http.delete("*/v1/keys/:id", async ({ request }) => {
        await recorder.record(request);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = renderWithProviders(<KeysSection />);

    const row = await screen.findByRole("row", { name: /laptop script/ });
    expect(
      within(row).getByText(new Date(LAST_USED).toLocaleString()),
    ).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "Rotate" }));
    const shown = await screen.findByRole("dialog", {
      name: "Copy your new key",
    });
    expect(within(shown).getByText(ROTATED)).toBeInTheDocument();
    await user.click(within(shown).getByRole("button", { name: "Done" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    // Revoking asks first; cancelling sends nothing.
    await user.click(within(row).getByRole("button", { name: "Revoke" }));
    const confirm = await screen.findByRole("dialog", {
      name: "Revoke this key?",
    });
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(recorder.writes()).toEqual([`POST /v1/keys/${KEY_ID}/rotate`]);

    await user.click(within(row).getByRole("button", { name: "Revoke" }));
    const again = await screen.findByRole("dialog", {
      name: "Revoke this key?",
    });
    await user.click(within(again).getByRole("button", { name: "Revoke key" }));
    await waitFor(() => {
      expect(recorder.writes()).toEqual([
        `POST /v1/keys/${KEY_ID}/rotate`,
        `DELETE /v1/keys/${KEY_ID}`,
      ]);
    });
    for (const sent of recorder.sent) {
      expect(sent.idempotencyKey).toBeTruthy();
    }
  });
});

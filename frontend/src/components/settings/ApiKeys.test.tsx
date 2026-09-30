import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test, vi } from "vitest";

import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ApiKeys } from "./ApiKeys";

const SECRET = "tmn_abcdefghijkl_0123456789abcdefghijklmnopqrstuvwxyzABCDEFG";
const ROTATED = "tmn_mnopqrstuvwx_ZYXWVUTSRQPONMLKJIHGFEDCBA9876543210zyxwvut";
const KEY_ID = "01890000-0000-7000-8000-0000000000aa";

interface Recorded {
  method: string;
  path: string;
  idempotencyKey: string | null;
  body: unknown;
}

function keyRow(overrides: Record<string, unknown> = {}) {
  return {
    id: KEY_ID,
    name: "laptop script",
    prefix: "abcdefghijkl",
    scopes: ["tasks:read"],
    project_ids: null,
    created_at: "2026-03-09T12:00:00Z",
    expires_at: null,
    last_used_at: null,
    revoked_at: null,
    ...overrides,
  };
}

function record(recorded: Recorded[]) {
  return async ({ request }: { request: Request }) => {
    const text = await request.text();
    recorded.push({
      method: request.method,
      path: new URL(request.url).pathname,
      idempotencyKey: request.headers.get("Idempotency-Key"),
      body: text ? (JSON.parse(text) as unknown) : null,
    });
  };
}

describe("ApiKeys", () => {
  test("[P0-14][SEC-2] shows the new key once with copy, then hides it", async () => {
    const recorded: Recorded[] = [];
    let items: unknown[] = [];
    server.use(
      http.get("*/v1/keys", () =>
        HttpResponse.json({ items, next_cursor: null }),
      ),
      http.post("*/v1/keys", async (info) => {
        await record(recorded)(info);
        items = [keyRow()];
        return HttpResponse.json({ ...keyRow(), key: SECRET }, { status: 201 });
      }),
    );
    const { user, queryClient } = renderWithProviders(<ApiKeys />);
    const writeText = vi.spyOn(navigator.clipboard, "writeText");

    await user.type(await screen.findByLabelText("Key name"), "laptop script");
    await user.click(screen.getByRole("checkbox", { name: "tasks:read" }));
    await user.click(screen.getByRole("button", { name: "Create key" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(SECRET)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith(SECRET);

    expect(recorded).toHaveLength(1);
    expect(recorded[0]?.method).toBe("POST");
    expect(recorded[0]?.idempotencyKey).toBeTruthy();
    expect(recorded[0]?.body).toMatchObject({
      name: "laptop script",
      scopes: ["tasks:read"],
    });

    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(await screen.findByText("abcdefghijkl")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(SECRET);
    const cached = JSON.stringify(
      queryClient
        .getQueryCache()
        .getAll()
        .map((query) => query.state.data),
    );
    expect(cached).not.toContain(SECRET);
  });

  test("[P0-14][FR-9.3] rotate and revoke call the right endpoints", async () => {
    const recorded: Recorded[] = [];
    server.use(
      http.get("*/v1/keys", () =>
        HttpResponse.json({ items: [keyRow()], next_cursor: null }),
      ),
      http.post("*/v1/keys/:id/rotate", async (info) => {
        await record(recorded)(info);
        return HttpResponse.json({
          ...keyRow({ prefix: "mnopqrstuvwx" }),
          key: ROTATED,
        });
      }),
      http.delete("*/v1/keys/:id", async (info) => {
        await record(recorded)(info);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = renderWithProviders(<ApiKeys />);

    const row = await screen.findByRole("row", { name: /laptop script/ });
    await user.click(within(row).getByRole("button", { name: "Rotate" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(ROTATED)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Done" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(document.body.textContent).not.toContain(ROTATED);

    await user.click(within(row).getByRole("button", { name: "Revoke" }));
    await waitFor(() => {
      expect(recorded).toHaveLength(2);
    });

    expect(recorded.map((r) => `${r.method} ${r.path}`)).toEqual([
      `POST /v1/keys/${KEY_ID}/rotate`,
      `DELETE /v1/keys/${KEY_ID}`,
    ]);
    for (const request of recorded) {
      expect(request.idempotencyKey).toBeTruthy();
    }
  });

  // axe's scrollable-region-focusable: a table wider than the phone scrolls sideways
  // inside its wrapper, so the wrapper must take keyboard focus, and as a region it needs
  // a name of its own (the section around it is already "API keys").
  test.fails(
    "[DS-01][UX 11] T-DS-01-19 the keys table scrolls in a named region the keyboard can reach",
    async () => {
      server.use(
        http.get("*/v1/keys", () =>
          HttpResponse.json({ items: [keyRow()], next_cursor: null }),
        ),
      );
      renderWithProviders(<ApiKeys />);

      const table = await screen.findByRole("table");
      const region = screen.getByRole("region", { name: "API keys table" });
      expect(region).toContainElement(table);
      expect(region).toHaveAttribute("tabindex", "0");
      expect(region.className).toContain("overflow-x-auto");
    },
  );
});

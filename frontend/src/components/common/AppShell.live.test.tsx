// The live socket follows the session (P0-22, ADR-0004). APP-11 (application test): the
// app opened `/ws` at start, on the sign-in page too, where the upgrade answers 403 and
// the socket kept retrying. The shell opens it on signed-in pages only and closes it when
// the visitor lands on /login.
import { http, HttpResponse } from "msw";
import { beforeEach, expect, test, vi } from "vitest";

import type { AccountOut } from "../../api/types.gen";
import { FakeSocket } from "../../test/fakeSocket";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

const ACCOUNT: AccountOut = {
  email: "owner@example.com",
  second_factor: "totp",
  totp_confirmed_at: "2026-03-01T09:00:00Z",
  user_id: "0192b3c4-0000-7000-8000-000000000001",
};

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  FakeSocket.reset();
  server.use(http.get("*/v1/auth/account", () => HttpResponse.json(ACCOUNT)));
});

test("[P0-22][ADR-0004] APP-11 the sign-in page opens no live socket", async () => {
  const { unmount } = await renderRoute("/login");
  expect(FakeSocket.instances).toHaveLength(0);
  unmount();
});

test("[P0-22][ADR-0004] APP-11 a signed-in page opens the live socket and closes it on leaving", async () => {
  const { router, unmount } = await renderRoute("/", { viewport: "laptop" });
  expect(FakeSocket.instances.length).toBeGreaterThan(0);
  const socket = FakeSocket.latest();
  expect(socket.url).toMatch(/\/ws$/);

  await router.navigate({ to: "/login" });
  expect(socket.closedByClient).toBe(true);
  expect(FakeSocket.instances.filter((s) => !s.closedByClient)).toHaveLength(0);
  unmount();
});

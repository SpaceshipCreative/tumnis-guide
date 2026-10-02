// Settings > Connections safety (P3-02 follow-up): the browser only goes to an https
// sign-in page (SEC-9; the MCP authorization spec serves every authorization server
// endpoint over HTTPS), and a settings save sends the version the form was loaded from, so
// an edit made elsewhere meanwhile answers 409 instead of being overwritten.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";

import { connectionRow, ConnectionsFake } from "../../../test/msw/connections";
import { server } from "../../../test/msw/server";
import { Recorder } from "../../../test/msw/settings";
import { renderWithProviders } from "../../../test/render";
import { Connections } from "./Connections";

const WORK = connectionRow();
const PERSONAL = connectionRow({
  id: "01890000-0000-7000-8000-0000000003b2",
  account_label: "Personal mail",
  status: "auth_required",
  status_detail: "Sign in again",
  last_success_at: null,
  version: 4,
});

function setup() {
  const recorder = new Recorder();
  const fake = new ConnectionsFake([WORK, PERSONAL]);
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

describe("Connections safety", () => {
  test.each([
    "http://sign-in.example.com/authorize?state=demo-state",
    "javascript:alert(1)",
    "data:text/html,hello",
    "/v1/somewhere-else",
  ])(
    "[P3-02][FR-14.4] a sign-in page that is not https (%s) is not opened",
    async (unsafe) => {
      const { user, opened } = setup();
      server.use(
        http.get("*/v1/connections/:connectionId/oauth/url", () =>
          HttpResponse.json({ authorize_url: unsafe }),
        ),
      );
      const personal = await screen.findByRole("group", {
        name: "Personal mail",
      });
      await user.click(
        within(personal).getByRole("button", { name: "Reconnect" }),
      );

      await waitFor(() => {
        expect(screen.getByRole("status")).toHaveTextContent(
          "The sign-in page is not secure (it must use https), so it was not opened.",
        );
      });
      expect(opened).toEqual([]);
    },
  );

  test("[P3-02][FR-14.4] a save sends the version the form was loaded from, so a concurrent edit answers 409", async () => {
    const { user, recorder, fake } = setup();
    const work = await screen.findByRole("group", { name: "Work mail" });
    await user.click(within(work).getByRole("button", { name: "Details" }));
    const backfill = within(work).getByRole("spinbutton", {
      name: "Days to look back",
    });
    await user.clear(backfill);
    await user.type(backfill, "60");

    // Someone else changes the connection; a read (here Sync now's answer) brings the
    // new version into the list while this form still holds the old values.
    const elsewhere = {
      ...WORK,
      settings: { ...WORK.settings, backfill_days: 90 },
      version: WORK.version + 1,
    };
    fake.rows = fake.rows.map((row) => (row.id === WORK.id ? elsewhere : row));
    await user.click(within(work).getByRole("button", { name: "Sync now" }));
    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(
        "Syncing Work mail.",
      );
    });

    await user.click(
      within(work).getByRole("button", { name: "Save settings" }),
    );

    expect(await within(work).findByRole("alert")).toHaveTextContent(
      "That changed elsewhere",
    );
    const patch = recorder.sent.find((s) => s.method === "PATCH");
    expect(patch?.body).toMatchObject({ version: WORK.version });
    expect(fake.rows.find((row) => row.id === WORK.id)).toEqual(elsewhere);
    // The form now shows the connection as it is, ready to edit again.
    await waitFor(() => {
      expect(
        within(work).getByRole("spinbutton", { name: "Days to look back" }),
      ).toHaveValue(90);
    });
  });
});

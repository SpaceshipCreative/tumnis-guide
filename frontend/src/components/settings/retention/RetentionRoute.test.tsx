// Settings > Retention is reachable at both layouts (P3-09 done checklist: "Settings
// reachable at 375 px"; AGENTS.md checks every UI change at 375 and 1280 px).
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import type { SettingSectionOut } from "../../../api/types.gen";
import { server } from "../../../test/msw/server";
import { renderRoute, type Viewport } from "../../../test/render";

const KEEP: SettingSectionOut = {
  section: "integrations.retention",
  values: { mode: "keep_until_project_purged", days: null },
  secrets_set: [],
  version: null,
};

async function opensRetention(viewport: Viewport): Promise<void> {
  server.use(
    http.get("*/v1/settings/integrations.retention", () =>
      HttpResponse.json(KEEP),
    ),
  );
  await renderRoute("/settings/retention", { viewport });
  expect(
    await screen.findByRole("radio", {
      name: "Keep until the project is purged",
    }),
  ).toBeChecked();
  expect(screen.getByRole("button", { name: "Save" })).toBeVisible();
}

test("[P3-09][SAAS-2] Settings opens Retention at 375 px", async () => {
  await opensRetention("phone");
});

test("[P3-09][SAAS-2] Settings opens Retention at 1280 px", async () => {
  await opensRetention("laptop");
});

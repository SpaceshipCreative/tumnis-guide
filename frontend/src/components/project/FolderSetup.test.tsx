import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";

import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { SHARE_ID, storageHandlers } from "../../test/msw/storage";
import { renderWithProviders } from "../../test/render";
import { FolderSetup, WRITE_RULE_HINT } from "./FolderSetup";

const PROJECT_ID = "01890000-0000-7000-8000-00000000a001";

function folderHandlers(recorder: Recorder) {
  return [
    ...storageHandlers(recorder),
    http.post(
      "*/v1/knowledge/projects/:id/existing-folder",
      async ({ request, params }) => {
        const sent = await recorder.record(request);
        const body = sent.body as { location_id: string; path: string };
        return HttpResponse.json({
          project_id: params.id,
          location_id: body.location_id,
          root_path: body.path,
          mode: "existing",
          version: 2,
        });
      },
    ),
    http.post(
      "*/v1/knowledge/projects/:id/folder/move",
      async ({ request, params }) => {
        await recorder.record(request);
        return HttpResponse.json(
          { workflow_id: `move:${String(params.id)}:1` },
          { status: 202 },
        );
      },
    ),
  ];
}

describe("FolderSetup", () => {
  test("[P3-14][FR-15.12] use a folder the user already keeps", async () => {
    const recorder = new Recorder();
    server.use(...folderHandlers(recorder));
    const { user } = renderWithProviders(
      <FolderSetup projectId={PROJECT_ID} />,
    );

    const form = screen
      .getByRole("heading", { name: "Use a folder you already keep" })
      .closest("form");
    if (!form) throw new Error("no existing-folder form");
    // The write rule is stated where the folder is chosen.
    expect(within(form).getByText(WRITE_RULE_HINT)).toBeInTheDocument();

    await within(form).findByRole("option", { name: "NAS share" });
    await user.selectOptions(within(form).getByLabelText("Location"), SHARE_ID);
    await user.type(
      within(form).getByLabelText("Folder path on that location"),
      "Clients/Acme",
    );
    await user.click(
      within(form).getByRole("button", { name: "Use this folder" }),
    );

    await waitFor(() => {
      expect(recorder.writes()).toEqual([
        `POST /v1/knowledge/projects/${PROJECT_ID}/existing-folder`,
      ]);
    });
    expect(recorder.sent[0]?.body).toEqual({
      location_id: SHARE_ID,
      path: "Clients/Acme",
    });
    expect(
      await screen.findByText(/Tumnis writes only in Clients\/Acme\/Tumnis\//),
    ).toBeInTheDocument();
  });

  test("[P3-14][FR-15.12] a move starts only after the dialog is confirmed", async () => {
    const recorder = new Recorder();
    server.use(...folderHandlers(recorder));
    const { user } = renderWithProviders(
      <FolderSetup projectId={PROJECT_ID} />,
    );

    const form = screen
      .getByRole("heading", { name: "Move the folder" })
      .closest("form");
    if (!form) throw new Error("no move form");
    await within(form).findByRole("option", { name: "NAS share" });
    await user.selectOptions(within(form).getByLabelText("Location"), SHARE_ID);
    await user.type(
      within(form).getByLabelText("Folder path on that location"),
      "tumnis/acme",
    );
    await user.click(within(form).getByRole("button", { name: "Move folder" }));

    // Cancel sends nothing.
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(recorder.writes()).toEqual([]);

    await user.click(within(form).getByRole("button", { name: "Move folder" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Start the move",
      }),
    );
    await waitFor(() => {
      expect(recorder.writes()).toEqual([
        `POST /v1/knowledge/projects/${PROJECT_ID}/folder/move`,
      ]);
    });
    expect(recorder.sent[0]?.body).toEqual({
      location_id: SHARE_ID,
      path: "tumnis/acme",
    });
    expect(await screen.findByText(/^Moving\./)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

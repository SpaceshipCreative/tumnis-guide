import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test, vi } from "vitest";

import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderWithProviders } from "../../test/render";
import { DeleteAtSourceDialog } from "./DeleteAtSourceDialog";

const DOC_ID = "01890000-0000-7000-8000-00000000d001";
// Obviously fake: the real token is made by the server at request time.
const TOKEN = "t".repeat(43);

function handlers(recorder: Recorder) {
  return [
    http.post(
      "*/v1/knowledge/documents/:id/delete-confirmation",
      async ({ request }) => {
        await recorder.record(request);
        return HttpResponse.json({
          confirm_token: TOKEN,
          expires_at: "2026-09-30T12:10:00Z",
        });
      },
    ),
    http.post(
      "*/v1/knowledge/documents/:id/delete-at-source",
      async ({ request }) => {
        await recorder.record(request);
        return HttpResponse.json(
          { outcome: "delete_at_source" },
          { status: 202 },
        );
      },
    ),
  ];
}

describe("DeleteAtSourceDialog", () => {
  test("[P3-14][FR-15.12] deleting an outside file needs the typed name and a reason", async () => {
    const recorder = new Recorder();
    server.use(...handlers(recorder));
    const onDone = vi.fn();
    const { user } = renderWithProviders(
      <DeleteAtSourceDialog
        documentId={DOC_ID}
        fileName="SOW.pdf"
        onDone={onDone}
        onCancel={vi.fn()}
      />,
    );

    const confirm = screen.getByRole("button", { name: "Delete at source" });
    expect(confirm).toBeDisabled();
    await user.type(
      screen.getByLabelText("Type the file name to confirm"),
      "SOW",
    );
    await user.type(screen.getByLabelText("Why delete it?"), "Old draft");
    expect(confirm).toBeDisabled();
    await user.type(
      screen.getByLabelText("Type the file name to confirm"),
      ".pdf",
    );
    expect(confirm).toBeEnabled();
    expect(recorder.writes()).toEqual([]);

    await user.click(confirm);
    await waitFor(() => {
      expect(onDone).toHaveBeenCalledWith("delete_at_source");
    });
    // The token is asked for only now, and sent once with the reason.
    expect(recorder.writes()).toEqual([
      `POST /v1/knowledge/documents/${DOC_ID}/delete-confirmation`,
      `POST /v1/knowledge/documents/${DOC_ID}/delete-at-source`,
    ]);
    expect(recorder.sent[1]?.body).toEqual({
      confirm_token: TOKEN,
      reason: "Old draft",
    });
  });

  test("[P3-14][FR-15.12] a refused delete says why and deletes nothing", async () => {
    const recorder = new Recorder();
    server.use(
      http.post(
        "*/v1/knowledge/documents/:id/delete-confirmation",
        async ({ request }) => {
          await recorder.record(request);
          return HttpResponse.json(
            {
              type: "about:blank",
              title: "Forbidden",
              status: 403,
              code: "forbidden",
              detail: "Only Tumnis's own files can go to its trash.",
            },
            { status: 403 },
          );
        },
      ),
    );
    const onDone = vi.fn();
    const { user } = renderWithProviders(
      <DeleteAtSourceDialog
        documentId={DOC_ID}
        fileName="SOW.pdf"
        onDone={onDone}
        onCancel={vi.fn()}
      />,
    );
    await user.type(
      screen.getByLabelText("Type the file name to confirm"),
      "SOW.pdf",
    );
    await user.type(screen.getByLabelText("Why delete it?"), "Old draft");
    await user.click(screen.getByRole("button", { name: "Delete at source" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Only Tumnis's own files can go to its trash.",
    );
    expect(onDone).not.toHaveBeenCalled();
    expect(recorder.writes()).toEqual([
      `POST /v1/knowledge/documents/${DOC_ID}/delete-confirmation`,
    ]);
  });
});

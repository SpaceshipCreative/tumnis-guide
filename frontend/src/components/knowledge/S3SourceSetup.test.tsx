import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";

import type { S3SourceCreated, S3SourceOut } from "../../api/types.gen";
import { makeProject } from "../../test/factories";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderWithProviders } from "../../test/render";
import { S3SourceSetup } from "./S3SourceSetup";

const PROJECT_ID = "01890000-0000-7000-8000-0000000003a1";
const SOURCE_ID = "01890000-0000-7000-8000-0000000003b1";
const TOKEN = "test-webhook-token";

function source(body: Record<string, unknown>): S3SourceOut {
  return {
    id: SOURCE_ID,
    provider: body.provider as S3SourceOut["provider"],
    endpoint: body.endpoint as string,
    region: body.region as string,
    bucket: body.bucket as string,
    path_style: true,
    trusted: false,
    prefixes: body.prefixes as S3SourceOut["prefixes"],
    capabilities: { checked: true, source: "minio_account_info" },
    warning: null,
    last_sync_at: null,
    version: 1,
  };
}

function handlers(recorder: Recorder, refuse: boolean) {
  let rows: S3SourceOut[] = [];
  return [
    http.get("*/v1/knowledge/s3-sources", () => HttpResponse.json(rows)),
    http.get("*/v1/projects", () =>
      HttpResponse.json({
        items: [makeProject({ id: PROJECT_ID, name: "Acme site" })],
        next_cursor: null,
      }),
    ),
    http.post("*/v1/knowledge/s3-sources", async ({ request }) => {
      const sent = await recorder.record(request);
      if (refuse) {
        return HttpResponse.json(
          {
            type: "about:blank",
            title: "Unprocessable",
            status: 422,
            code: "key_not_read_only",
            detail: "Use a read-only key for this bucket (key_can_write).",
          },
          { status: 422 },
        );
      }
      const created: S3SourceCreated = {
        ...source(sent.body as Record<string, unknown>),
        webhook_token: TOKEN,
        webhook_path: `/v1/webhooks/minio/${SOURCE_ID}`,
        minio_commands: [
          `mc admin config set ALIAS notify_webhook:t endpoint=x auth_token=${TOKEN}`,
        ],
      };
      rows = [source(sent.body as Record<string, unknown>)];
      return HttpResponse.json(created, { status: 201 });
    }),
  ];
}

async function fill(user: ReturnType<typeof renderWithProviders>["user"]) {
  await user.type(
    screen.getByLabelText("Endpoint"),
    "https://minio.example.lan:9000",
  );
  await user.type(screen.getByLabelText("Bucket"), "tumnis-docs");
  await user.type(screen.getByLabelText("Access key"), "test-key-id");
  await user.type(screen.getByLabelText("Secret key"), "test-secret");
  await user.type(screen.getByLabelText("Prefix 1"), "acme/");
  await screen.findByRole("option", { name: "Acme site" });
  await user.selectOptions(
    screen.getByLabelText("Project for prefix 1"),
    PROJECT_ID,
  );
}

describe("S3SourceSetup", () => {
  test("[P3-13][FR-15.11] links a bucket and shows the webhook token once", async () => {
    const recorder = new Recorder();
    server.use(...handlers(recorder, false));
    const { user } = renderWithProviders(<S3SourceSetup />);

    await fill(user);
    await user.click(screen.getByRole("button", { name: "Link bucket" }));

    await waitFor(() => {
      expect(recorder.writes()).toEqual(["POST /v1/knowledge/s3-sources"]);
    });
    expect(recorder.sent[0]?.body).toEqual({
      provider: "minio",
      endpoint: "https://minio.example.lan:9000",
      region: "us-east-1",
      bucket: "tumnis-docs",
      access_key: "test-key-id",
      secret_key: "test-secret",
      path_style: true,
      trusted: false,
      prefixes: [{ prefix: "acme/", project_id: PROJECT_ID }],
    });
    expect(await screen.findByText(TOKEN)).toBeInTheDocument();
    expect(
      await screen.findByText("tumnis-docs", { selector: "p" }),
    ).toBeInTheDocument();
    // The secret never stays on screen.
    expect(screen.getByLabelText("Secret key")).toHaveValue("");
  });

  test("[P3-13][FR-15.11] a writable key is refused in plain words", async () => {
    const recorder = new Recorder();
    server.use(...handlers(recorder, true));
    const { user } = renderWithProviders(<S3SourceSetup />);

    await fill(user);
    await user.click(screen.getByRole("button", { name: "Link bucket" }));

    expect(
      await screen.findByText(/That key can write or delete/),
    ).toBeInTheDocument();
    expect(screen.queryByText(TOKEN)).not.toBeInTheDocument();
  });
});

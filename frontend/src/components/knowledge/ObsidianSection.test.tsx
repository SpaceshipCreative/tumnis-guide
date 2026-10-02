import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";

import type { VaultOut } from "../../api/types.gen";
import { makeProject } from "../../test/factories";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderWithProviders } from "../../test/render";
import { ObsidianSection } from "./ObsidianSection";

const ACME = makeProject({
  id: "01890000-0000-7000-8000-0000000000a1",
  name: "Acme",
});

function vault(overrides: Partial<VaultOut>): VaultOut {
  return {
    id: "01890000-0000-7000-8000-0000000000e1",
    mode: "folder",
    folder_path: "/vaults/notes",
    remote: null,
    branch: "main",
    folders: [],
    unmapped: "workspace",
    clippings_folder: "Clippings",
    extra_excludes: [],
    deploy_public_key: null,
    host_key_sha256: null,
    status: "ok",
    last_error: null,
    last_sync_at: null,
    version: 1,
    ...overrides,
  };
}

const BROKEN = vault({
  id: "01890000-0000-7000-8000-0000000000e2",
  mode: "git",
  folder_path: null,
  remote: "ssh://git@git.example.com/notes.git",
  status: "error",
  last_error: "host_key_changed",
});
const DRAFT = vault({
  id: "01890000-0000-7000-8000-0000000000e3",
  folder_path: null,
  status: "pending",
});

function handlers(recorder: Recorder, listed: VaultOut[]) {
  let vaults = listed;
  return [
    http.get("*/v1/projects", () =>
      HttpResponse.json({ items: [ACME], next_cursor: null }),
    ),
    http.get("*/v1/knowledge/obsidian/vaults", () =>
      HttpResponse.json({ folder_allowed: true, vaults }),
    ),
    http.post("*/v1/knowledge/obsidian/vaults", async ({ request }) => {
      await recorder.record(request);
      vaults = [...vaults, DRAFT];
      return HttpResponse.json(DRAFT, { status: 201 });
    }),
    http.delete(
      "*/v1/knowledge/obsidian/vaults/:id",
      async ({ request, params }) => {
        await recorder.record(request);
        vaults = vaults.filter((v) => v.id !== params.id);
        return new HttpResponse(null, { status: 204 });
      },
    ),
    http.post(
      "*/v1/knowledge/obsidian/vaults/:id/preview",
      async ({ request }) => {
        await recorder.record(request);
        return HttpResponse.json(
          { preview_id: `obsidian-preview:${DRAFT.id}:1` },
          { status: 202 },
        );
      },
    ),
    http.get("*/v1/knowledge/obsidian/vaults/:id/preview/:previewId", () =>
      HttpResponse.json({
        status: "done",
        error: null,
        rows: [
          {
            path: "Clients/Acme/Kickoff.md",
            project_id: ACME.id,
            ignored: false,
            untrusted: false,
          },
        ],
      }),
    ),
    http.post(
      "*/v1/knowledge/obsidian/vaults/:id/connect",
      async ({ request }) => {
        await recorder.record(request);
        const connecting = {
          ...DRAFT,
          folder_path: "/vaults/notes",
          status: "connecting" as const,
        };
        vaults = vaults.map((v) => (v.id === DRAFT.id ? connecting : v));
        return HttpResponse.json(connecting, { status: 202 });
      },
    ),
  ];
}

describe("ObsidianSection", () => {
  test("[P3-12][FR-15.10] lists vaults with status and last error, and disconnects one", async () => {
    const recorder = new Recorder();
    server.use(...handlers(recorder, [BROKEN, DRAFT]));
    const { user } = renderWithProviders(<ObsidianSection />);

    const item = (await screen.findByText(BROKEN.remote ?? "")).closest("li");
    if (!item) throw new Error("the vault is listed");
    expect(within(item).getByText("error")).toBeInTheDocument();
    expect(
      within(item).getByText("Last error: host_key_changed"),
    ).toBeInTheDocument();
    // A pending draft is not a connected vault.
    expect(screen.getAllByRole("listitem")).toHaveLength(1);

    await user.click(within(item).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => {
      expect(recorder.writes()).toEqual([
        `DELETE /v1/knowledge/obsidian/vaults/${BROKEN.id}`,
      ]);
    });
    await waitFor(() => {
      expect(screen.queryByText(BROKEN.remote ?? "")).not.toBeInTheDocument();
    });
  });

  test("[P3-12][FR-15.10] a folder vault is drafted, previewed on the worker, then connected", async () => {
    const recorder = new Recorder();
    server.use(...handlers(recorder, []));
    const { user } = renderWithProviders(<ObsidianSection />);

    await user.click(
      await screen.findByRole("button", { name: "Add a vault" }),
    );
    await user.type(screen.getByLabelText("Vault folder"), "/vaults/notes");
    await user.click(screen.getByRole("button", { name: "Add a folder rule" }));
    await user.type(screen.getByLabelText("Folder"), "Clients/Acme");
    await user.selectOptions(screen.getByLabelText("Project"), ACME.id);
    await user.click(
      screen.getByRole("button", { name: "Preview the mapping" }),
    );

    const table = within(
      await screen.findByRole("region", { name: "Mapping preview" }),
    );
    expect(table.getByText("Clients/Acme/Kickoff.md")).toBeInTheDocument();
    expect(table.getByText("Acme")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => {
      expect(recorder.writes()).toEqual([
        "POST /v1/knowledge/obsidian/vaults",
        `POST /v1/knowledge/obsidian/vaults/${DRAFT.id}/preview`,
        `POST /v1/knowledge/obsidian/vaults/${DRAFT.id}/connect`,
      ]);
    });
    const settings = {
      mode: "folder",
      folder_path: "/vaults/notes",
      remote: null,
      branch: "main",
      folders: [{ folder: "Clients/Acme", project_id: ACME.id }],
      unmapped: "workspace",
      clippings_folder: "Clippings",
      known_hosts: null,
    };
    expect(recorder.sent[0]?.body).toEqual({ mode: "folder" });
    expect(recorder.sent[1]?.body).toEqual(settings);
    expect(recorder.sent[2]?.body).toEqual(settings);
    expect(recorder.sent.every((s) => s.idempotencyKey)).toBe(true);

    // The setup closes and the vault is listed as connecting.
    expect(
      await screen.findByRole("button", { name: "Add a vault" }),
    ).toBeInTheDocument();
    const item = screen.getByText("/vaults/notes").closest("li");
    if (!item) throw new Error("the vault is listed");
    expect(within(item).getByText("connecting")).toBeInTheDocument();
  });

  test("[P3-12][FR-15.10] a draft left from an earlier setup is shown and can be discarded", async () => {
    // CodeRabbit on #174: a pending vault this page doesn't hold (the setup was left
    // or the page reloaded) may hold a deploy key, so it must be visible and removable.
    const recorder = new Recorder();
    server.use(...handlers(recorder, [BROKEN, DRAFT]));
    const { user } = renderWithProviders(<ObsidianSection />);

    const left = within(
      await screen.findByRole("region", { name: "Unfinished setups" }),
    );
    // Still not a connected vault.
    expect(screen.getAllByRole("listitem")).toHaveLength(1);

    await user.click(left.getByRole("button", { name: "Discard" }));
    await waitFor(() => {
      expect(recorder.writes()).toEqual([
        `DELETE /v1/knowledge/obsidian/vaults/${DRAFT.id}`,
      ]);
    });
    await waitFor(() => {
      expect(
        screen.queryByRole("region", { name: "Unfinished setups" }),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByText(BROKEN.remote ?? "")).toBeInTheDocument();
  });

  test("[P3-12][FR-15.10] removing a vault sends one DELETE however often it is clicked", async () => {
    // CodeRabbit on #177: each click sends a new idempotency key, so a second click
    // while the first DELETE is in flight would answer 404 and show it as an error.
    const recorder = new Recorder();
    let release = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      ...handlers(recorder, [BROKEN, DRAFT]),
      http.delete("*/v1/knowledge/obsidian/vaults/:id", async ({ request }) => {
        await recorder.record(request);
        await held;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { user } = renderWithProviders(<ObsidianSection />);

    const item = (await screen.findByText(BROKEN.remote ?? "")).closest("li");
    if (!item) throw new Error("the vault is listed");
    const disconnect = within(item).getByRole("button", { name: "Disconnect" });
    const discard = screen.getByRole("button", { name: "Discard" });
    await user.click(disconnect);
    await waitFor(() => {
      expect(disconnect).toBeDisabled();
    });
    expect(discard).toBeDisabled();
    await user.click(disconnect);
    await user.click(discard);
    release();
    await waitFor(() => {
      expect(disconnect).toBeEnabled();
    });
    expect(recorder.writes()).toEqual([
      `DELETE /v1/knowledge/obsidian/vaults/${BROKEN.id}`,
    ]);
  });
});

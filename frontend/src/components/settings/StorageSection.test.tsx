import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { SHARE_ID, storageHandlers } from "../../test/msw/storage";
import { renderWithProviders } from "../../test/render";
import { StorageSection } from "./StorageSection";

async function item(name: string): Promise<HTMLElement> {
  const label = await screen.findByText(name, { selector: "p" });
  const row = label.closest("li");
  if (!row) throw new Error(`${name} is not in a list item`);
  return row;
}

describe("StorageSection", () => {
  test.fails("[P1-14][FR-15.7] T-P1-14-15 locations and default", async () => {
    const recorder = new Recorder();
    server.use(...storageHandlers(recorder));
    const { user } = renderWithProviders(<StorageSection />);

    // The list: the default is marked; an offline share says why in plain words.
    const disk = await item("Projects disk");
    expect(within(disk).getByText("Default")).toBeInTheDocument();
    expect(within(disk).getByText("Online")).toBeInTheDocument();
    const share = await item("NAS share");
    expect(
      within(share).getByText("Folder offline: marker file missing"),
    ).toBeInTheDocument();

    // Add a server path.
    await user.type(screen.getByLabelText("Name"), "Archive");
    await user.selectOptions(screen.getByLabelText("Kind"), "server_path");
    await user.type(screen.getByLabelText("Folder path"), "/data/archive");
    await user.click(screen.getByRole("button", { name: "Add location" }));
    await waitFor(() => {
      expect(recorder.writes()).toEqual(["POST /v1/knowledge/locations"]);
    });
    expect(recorder.sent[0]?.body).toEqual({
      name: "Archive",
      kind: "server_path",
      root: "/data/archive",
      is_default: false,
    });
    expect(await item("Archive")).toBeInTheDocument();

    // Add an S3 location: its credentials go in the request, never back on screen.
    await user.type(screen.getByLabelText("Name"), "MinIO");
    await user.selectOptions(screen.getByLabelText("Kind"), "s3");
    await user.type(
      screen.getByLabelText("Bucket and prefix"),
      "tumnis/projects",
    );
    await user.type(
      screen.getByLabelText("Endpoint"),
      "https://minio.lan:9000",
    );
    await user.type(screen.getByLabelText("Access key"), "AKIAEXAMPLE");
    await user.type(screen.getByLabelText("Secret key"), "s3cret-example");
    await user.click(screen.getByRole("button", { name: "Add location" }));
    await waitFor(() => {
      expect(recorder.writes()).toHaveLength(2);
    });
    expect(recorder.sent[1]?.body).toMatchObject({
      name: "MinIO",
      kind: "s3",
      root: "tumnis/projects",
      s3: {
        endpoint: "https://minio.lan:9000",
        access_key: "AKIAEXAMPLE",
        secret_key: "s3cret-example",
      },
    });
    expect(screen.queryByDisplayValue("s3cret-example")).toBeNull();

    // Test the share's connection: its status updates.
    await user.click(
      within(share).getByRole("button", { name: "Test connection" }),
    );
    await waitFor(() => {
      expect(recorder.writes()).toContain(
        `POST /v1/knowledge/locations/${SHARE_ID}/test`,
      );
    });
    expect(
      await within(await item("NAS share")).findByText("Online"),
    ).toBeInTheDocument();

    // Make the share the default: the version read goes with it.
    await user.click(
      within(await item("NAS share")).getByRole("button", {
        name: "Make default",
      }),
    );
    await waitFor(() => {
      expect(recorder.writes()).toContain(
        `POST /v1/knowledge/locations/${SHARE_ID}/default`,
      );
    });
    const sentDefault = recorder.sent.find((s) => s.path.endsWith("/default"));
    expect(sentDefault?.body).toEqual({ version: 4 });
    await waitFor(async () => {
      expect(
        within(await item("NAS share")).getByText("Default"),
      ).toBeInTheDocument();
    });
    expect(
      within(await item("Projects disk")).queryByText("Default"),
    ).toBeNull();
    for (const sent of recorder.sent) {
      expect(sent.idempotencyKey).toBeTruthy();
    }
  });
});

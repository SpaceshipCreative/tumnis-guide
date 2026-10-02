import { render, screen, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";

import { ObsidianSetup, type PreviewRow } from "./ObsidianSetup";

// Obviously fake values: a fingerprint and key made of one repeated letter.
const SHOWN = `SHA256:${"Q".repeat(43)}`;
const LINE = `git.example.com ssh-ed25519 ${"A".repeat(68)}`;
const PROJECTS = [
  { id: "p-acme", name: "Acme" },
  { id: "p-lab", name: "Lab" },
];
const ROWS: PreviewRow[] = [
  {
    path: "Clients/Acme/Kickoff.md",
    projectId: "p-acme",
    ignored: false,
    untrusted: false,
  },
  {
    path: "Clippings/Article.md",
    projectId: null,
    ignored: false,
    untrusted: true,
  },
  {
    path: "Scratch/Loose.md",
    projectId: null,
    ignored: true,
    untrusted: false,
  },
];

function setup(hosted = false) {
  const handlers = {
    onProbeHostKey: vi
      .fn()
      .mockResolvedValue({ sha256: SHOWN, knownHosts: LINE }),
    onPreview: vi.fn().mockResolvedValue(ROWS),
    onConnect: vi.fn().mockResolvedValue(undefined),
  };
  render(
    <ObsidianSetup
      hosted={hosted}
      projects={PROJECTS}
      deployKey={`ssh-ed25519 ${"B".repeat(68)} tumnis`}
      {...handlers}
    />,
  );
  return { user: userEvent.setup(), ...handlers };
}

describe("ObsidianSetup", () => {
  test("[P3-12][FR-15.10] hosted mode offers the Git source only", () => {
    setup(true);
    expect(screen.queryByLabelText("Vault folder")).not.toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Git remote")).toBeInTheDocument();
  });

  test("[P3-12][FR-15.10] a folder vault connects only after a preview of its settings", async () => {
    const { user, onPreview, onConnect } = setup();
    const connect = screen.getByRole("button", { name: "Connect" });
    await user.type(screen.getByLabelText("Vault folder"), "/vaults/notes");
    expect(connect).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Add a folder rule" }));
    await user.type(screen.getByLabelText("Folder"), "Clients/Acme");
    await user.selectOptions(screen.getByLabelText("Project"), "p-acme");
    await user.click(
      screen.getByRole("button", { name: "Preview the mapping" }),
    );

    const table = within(
      screen.getByRole("region", { name: "Mapping preview" }),
    );
    expect(table.getByText("Acme")).toBeInTheDocument();
    expect(table.getByText("Workspace knowledge base")).toBeInTheDocument();
    expect(table.getByText("Left out")).toBeInTheDocument();
    expect(table.getByText("Untrusted")).toBeInTheDocument();
    expect(onPreview).toHaveBeenCalledWith({
      settings: expect.objectContaining({
        mode: "folder",
        folderPath: "/vaults/notes",
        folders: [{ folder: "Clients/Acme", projectId: "p-acme" }],
      }) as unknown,
      knownHosts: null,
    });

    // Any change makes the preview stale until it runs again.
    await user.clear(
      screen.getByLabelText("Web clippings folder (its notes are untrusted)"),
    );
    await user.type(
      screen.getByLabelText("Web clippings folder (its notes are untrusted)"),
      "Web",
    );
    expect(connect).toBeDisabled();
    await user.click(
      screen.getByRole("button", { name: "Preview the mapping" }),
    );
    await user.click(connect);
    expect(onConnect).toHaveBeenCalledTimes(1);
    expect(onConnect).toHaveBeenCalledWith({
      settings: expect.objectContaining({ clippingsFolder: "Web" }) as unknown,
      knownHosts: null,
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Connected");
  });

  test("[P3-12][Data flow 1] a Git host key is trusted only once its fingerprint is confirmed", async () => {
    const { user, onProbeHostKey, onPreview, onConnect } = setup();
    await user.click(screen.getByRole("radio", { name: "A Git remote" }));
    expect(screen.getByText(/Leave write access off/)).toBeInTheDocument();
    await user.type(
      screen.getByLabelText("Git remote"),
      "ssh://git@git.example.com/notes.git",
    );
    const preview = screen.getByRole("button", { name: "Preview the mapping" });
    expect(preview).toBeDisabled();

    await user.click(
      screen.getByRole("button", { name: "Check the server's host key" }),
    );
    expect(onProbeHostKey).toHaveBeenCalledWith(
      "ssh://git@git.example.com/notes.git",
    );
    expect(screen.getByText(SHOWN)).toBeInTheDocument();
    expect(preview).toBeDisabled(); // shown, not yet trusted

    await user.type(
      screen.getByLabelText("Fingerprint from the server"),
      SHOWN,
    );
    await user.click(screen.getByRole("button", { name: "Trust this server" }));
    expect(screen.getByText(/Host key trusted/)).toBeInTheDocument();

    await user.click(preview);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    const expected = {
      settings: expect.objectContaining({
        mode: "git",
        remote: "ssh://git@git.example.com/notes.git",
        branch: "main",
      }) as unknown,
      knownHosts: LINE,
    };
    expect(onPreview).toHaveBeenCalledWith(expected);
    expect(onConnect).toHaveBeenCalledWith(expected);
  });

  test("[P3-12][Data flow 1] a pasted known_hosts line stands in for the probe; errors show", async () => {
    const { user, onConnect } = setup();
    onConnect.mockRejectedValueOnce(
      new Error("This deploy key can write; use a read-only key"),
    );
    await user.click(screen.getByRole("radio", { name: "A Git remote" }));
    await user.type(
      screen.getByLabelText("Git remote"),
      "ssh://git@git.example.com/notes.git",
    );
    await user.click(
      screen.getByLabelText("Or paste the server's known_hosts line"),
    );
    await user.paste(LINE);
    await user.click(
      screen.getByRole("button", { name: "Preview the mapping" }),
    );
    await user.click(screen.getByRole("button", { name: "Connect" }));
    expect(onConnect).toHaveBeenCalledWith({
      settings: expect.objectContaining({ mode: "git" }) as unknown,
      knownHosts: LINE,
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("read-only key");
  });
});

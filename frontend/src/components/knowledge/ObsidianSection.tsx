// Settings > Obsidian (P3-12, FR-15.10): the workspace's Obsidian vaults, and ObsidianSetup
// wired to the knowledge API.
//
// - The list says whether a folder vault is offered (never in hosted mode).
// - A vault is made on the first step that needs one (a pending draft): a Git vault when
//   the host key is first probed, so its read-only deploy key shows before Preview clones
//   the remote; a folder vault when it is first previewed. Switching mode makes a new draft
//   and removes the old one.
// - Preview runs on the worker: POST starts it, then the answer is polled.
// - Connect saves the settings and pinned host key; the worker clones, probes the key and
//   syncs. The list shows each vault's status and last error.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  knowledgeGetObsidianPreviewOptions,
  knowledgeListObsidianVaultsOptions,
  knowledgeListObsidianVaultsQueryKey,
  projectsListProjectsOptions,
} from "../../api/@tanstack/react-query.gen";
import type {
  HostKeyOut,
  PreviewStarted,
  VaultOut,
  VaultSettingsIn,
} from "../../api/types.gen";
import { ApiError, apiWrite } from "../../lib/fetch";
import { badge } from "../common/ui";
import { PROJECT_LIST } from "../project/CreateProjectDialog";
import {
  BUTTON,
  CARD,
  ERROR,
  HEADING,
  HINT,
  SECONDARY,
  SECTION,
} from "../settings/styles";
import {
  ObsidianSetup,
  type PreviewRow,
  type VaultConnect,
  type VaultMode,
} from "./ObsidianSetup";

const POLL_MS = 1000;
const POLL_LIMIT = 120; // two minutes for a first clone of a large vault

function key(): string {
  return crypto.randomUUID();
}

function problem(error: unknown): Error {
  if (error instanceof ApiError) {
    return new Error(error.problem.detail ?? error.problem.title);
  }
  return error instanceof Error ? error : new Error("Something went wrong");
}

function body(request: VaultConnect): VaultSettingsIn {
  const s = request.settings;
  return {
    mode: s.mode,
    folder_path: s.mode === "folder" ? s.folderPath.trim() : null,
    remote: s.mode === "git" ? s.remote.trim() : null,
    branch: s.branch.trim() || "main",
    folders: s.folders
      .filter((rule) => rule.folder.trim() && rule.projectId)
      .map((rule) => ({
        folder: rule.folder.trim(),
        project_id: rule.projectId,
      })),
    unmapped: s.unmapped,
    clippings_folder: s.clippingsFolder.trim() || "Clippings",
    known_hosts: request.knownHosts,
  };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function ObsidianSection() {
  const client = useQueryClient();
  const vaults = useQuery(knowledgeListObsidianVaultsOptions());
  const projects = useQuery(projectsListProjectsOptions(PROJECT_LIST));
  const [draft, setDraft] = useState<VaultOut | null>(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = () =>
    client.invalidateQueries({
      queryKey: knowledgeListObsidianVaultsQueryKey(),
    });

  async function draftFor(mode: VaultMode): Promise<VaultOut> {
    if (draft?.mode === mode) return draft;
    const made = await apiWrite<VaultOut>({
      kind: "create",
      method: "POST",
      path: "/knowledge/obsidian/vaults",
      body: { mode },
      idempotencyKey: key(),
    });
    if (draft) await remove(draft.id);
    setDraft(made);
    return made;
  }

  async function remove(id: string): Promise<void> {
    await apiWrite<undefined>({
      kind: "create",
      method: "DELETE",
      path: `/knowledge/obsidian/vaults/${id}`,
      body: {},
      idempotencyKey: key(),
    });
  }

  async function probe(remote: string) {
    try {
      await draftFor("git");
      const found = await apiWrite<HostKeyOut>({
        kind: "create",
        method: "POST",
        path: "/knowledge/obsidian/host-key/probe",
        body: { remote },
        idempotencyKey: key(),
      });
      return { sha256: found.sha256, knownHosts: found.known_hosts };
    } catch (e) {
      throw problem(e);
    }
  }

  async function preview(request: VaultConnect): Promise<PreviewRow[]> {
    try {
      const vault = await draftFor(request.settings.mode);
      const started = await apiWrite<PreviewStarted>({
        kind: "create",
        method: "POST",
        path: `/knowledge/obsidian/vaults/${vault.id}/preview`,
        body: body(request),
        idempotencyKey: key(),
      });
      for (let n = 0; n < POLL_LIMIT; n += 1) {
        const out = await client.query({
          ...knowledgeGetObsidianPreviewOptions({
            path: {
              connection_id: vault.id,
              preview_id: started.preview_id,
            },
          }),
          staleTime: 0,
        });
        if (out.status === "done") {
          return out.rows.map((row) => ({
            path: row.path,
            projectId: row.project_id ?? null,
            ignored: row.ignored,
            untrusted: row.untrusted,
          }));
        }
        if (out.status === "failed") {
          throw new Error(
            `The vault could not be read (${out.error ?? "unknown"}).`,
          );
        }
        await sleep(POLL_MS);
      }
      throw new Error("The preview is taking too long; try again.");
    } catch (e) {
      throw problem(e);
    }
  }

  async function connect(request: VaultConnect): Promise<void> {
    try {
      const vault = await draftFor(request.settings.mode);
      await apiWrite<VaultOut>({
        kind: "create",
        method: "POST",
        path: `/knowledge/obsidian/vaults/${vault.id}/connect`,
        body: body(request),
        idempotencyKey: key(),
      });
      setDraft(null);
      setAdding(false);
      await refresh();
    } catch (e) {
      throw problem(e);
    }
  }

  async function disconnect(id: string) {
    setError(null);
    try {
      await remove(id);
      await refresh();
    } catch (e) {
      setError(problem(e).message);
    }
  }

  const connected = (vaults.data?.vaults ?? []).filter(
    (v) => v.id !== draft?.id && v.status !== "pending",
  );
  return (
    <section className={SECTION} aria-labelledby="obsidian-heading">
      <h2 id="obsidian-heading" className={HEADING}>
        Obsidian vaults
      </h2>
      <p className={HINT}>
        Notes are read from the vault and kept read-only here; change them in
        Obsidian.
      </p>
      {connected.length > 0 && (
        <ul className="flex flex-col gap-2">
          {connected.map((v) => (
            <li key={v.id} className={CARD}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">
                  {v.mode === "git" ? v.remote : v.folder_path}
                </span>
                <span
                  className={badge(
                    v.status === "ok"
                      ? "success"
                      : v.status === "error"
                        ? "danger"
                        : "neutral",
                  )}
                >
                  {v.status}
                </span>
                <button
                  type="button"
                  className={SECONDARY}
                  onClick={() => void disconnect(v.id)}
                >
                  Disconnect
                </button>
              </div>
              {v.last_error && (
                <p className={ERROR}>Last error: {v.last_error}</p>
              )}
              {v.last_sync_at && (
                <p className={HINT}>
                  Last synced {new Date(v.last_sync_at).toLocaleString()}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
      {error && <p className={ERROR}>{error}</p>}
      {adding ? (
        <ObsidianSetup
          hosted={vaults.data ? !vaults.data.folder_allowed : true}
          projects={(projects.data?.items ?? []).map((p) => ({
            id: p.id,
            name: p.name,
          }))}
          deployKey={draft?.mode === "git" ? draft.deploy_public_key : null}
          onProbeHostKey={probe}
          onPreview={preview}
          onConnect={connect}
        />
      ) : (
        <button
          type="button"
          className={BUTTON}
          disabled={!vaults.data}
          onClick={() => {
            setAdding(true);
          }}
        >
          Add a vault
        </button>
      )}
    </section>
  );
}

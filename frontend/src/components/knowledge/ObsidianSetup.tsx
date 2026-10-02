// Settings > Connections > Obsidian vault (P3-12, FR-15.10): where the vault is read from
// and how its notes map to projects, with a dry-run preview before anything is synced.
//
// - Source: a read-only mounted folder or a Git remote. Hosted mode offers Git only (the
//   folder option is not shown).
// - Git: the read-only deploy key's public half to add on the Git host, then the server's
//   host key, confirmed by typing the fingerprint read on the server (HostKey, Scott
//   decision 82) or by pasting a known_hosts line. Nothing is trusted automatically.
// - Mapping: folder rules (longest prefix wins), what happens to unmapped notes, and the
//   clippings folder (its notes are untrusted).
// - Preview runs the mapping over the vault's notes on the worker; Connect stays disabled
//   until a preview has run for the settings (and host key) as they are now.
import { useId, useState } from "react";

import {
  badge,
  TABLE,
  TABLE_BODY,
  TABLE_CELL,
  TABLE_HEAD,
  TABLE_HEADER_CELL,
} from "../common/ui";
import { HostKey } from "../settings/storage/HostKey";
import {
  BUTTON,
  CARD,
  ERROR,
  HEADING,
  HINT,
  INPUT,
  LABEL,
  SECONDARY,
  SECTION,
} from "../settings/styles";

export type VaultMode = "folder" | "git";

export interface FolderRule {
  folder: string;
  projectId: string;
}

export interface VaultSettings {
  mode: VaultMode;
  folderPath: string;
  remote: string;
  branch: string;
  folders: FolderRule[];
  unmapped: "workspace" | "ignore";
  clippingsFolder: string;
}

export interface PreviewRow {
  path: string;
  projectId: string | null;
  ignored: boolean;
  untrusted: boolean;
}

export interface ProbedHostKey {
  sha256: string;
  knownHosts: string;
}

/** What Connect sends: the settings, and for Git the confirmed or pasted host key. */
export interface VaultConnect {
  settings: VaultSettings;
  knownHosts: string | null;
}

export interface ObsidianSetupProps {
  hosted: boolean;
  projects: { id: string; name: string }[];
  /** The public half of the connection's read-only deploy key (Git). */
  deployKey?: string | null;
  onProbeHostKey: (remote: string) => Promise<ProbedHostKey>;
  onPreview: (request: VaultConnect) => Promise<PreviewRow[]>;
  onConnect: (request: VaultConnect) => Promise<void>;
}

const DEFAULTS: VaultSettings = {
  mode: "folder",
  folderPath: "",
  remote: "",
  branch: "main",
  folders: [],
  unmapped: "workspace",
  clippingsFolder: "Clippings",
};

function failure(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : "Something went wrong";
}

export function ObsidianSetup({
  hosted,
  projects,
  deployKey = null,
  onProbeHostKey,
  onPreview,
  onConnect,
}: ObsidianSetupProps) {
  const ids = {
    folderPath: useId(),
    remote: useId(),
    branch: useId(),
    pasted: useId(),
    unmapped: useId(),
    clippings: useId(),
  };
  const [settings, setSettings] = useState<VaultSettings>(
    hosted ? { ...DEFAULTS, mode: "git" } : DEFAULTS,
  );
  const [probed, setProbed] = useState<ProbedHostKey | null>(null);
  const [confirmed, setConfirmed] = useState<string | null>(null);
  const [pasted, setPasted] = useState("");
  const [preview, setPreview] = useState<{
    asked: VaultConnect;
    rows: PreviewRow[];
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const mode: VaultMode = hosted ? "git" : settings.mode;
  const names = new Map(projects.map((p) => [p.id, p.name]));
  const knownHosts = confirmed ?? (pasted.trim() || null);
  const sourceReady =
    mode === "folder"
      ? settings.folderPath.trim() !== ""
      : settings.remote.trim() !== "" &&
        settings.branch.trim() !== "" &&
        knownHosts !== null;
  const hostsFor = mode === "git" ? knownHosts : null;
  const previewCurrent =
    preview !== null &&
    preview.asked.settings === settings &&
    preview.asked.knownHosts === hostsFor;

  function change(patch: Partial<VaultSettings>) {
    setSettings((held) => ({ ...held, ...patch }));
    setDone(false);
  }

  function changeRemote(remote: string) {
    change({ remote });
    setProbed(null);
    setConfirmed(null);
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(failure(caught));
    } finally {
      setBusy(false);
    }
  }

  function rule(index: number, patch: Partial<FolderRule>) {
    change({
      folders: settings.folders.map((held, at) =>
        at === index ? { ...held, ...patch } : held,
      ),
    });
  }

  return (
    <section className={SECTION} aria-labelledby={`${ids.folderPath}-heading`}>
      <h2 id={`${ids.folderPath}-heading`} className={HEADING}>
        Obsidian vault
      </h2>

      <fieldset className={CARD}>
        <legend className="text-base font-semibold">
          Where Tumnis reads the vault
        </legend>
        {hosted ? (
          <p className={HINT}>
            This server reads a vault from a Git remote only.
          </p>
        ) : (
          <div className="flex flex-wrap gap-4">
            {(["folder", "git"] as const).map((option) => (
              <label key={option} className="flex min-h-11 items-center gap-2">
                <input
                  type="radio"
                  name={`${ids.folderPath}-mode`}
                  value={option}
                  checked={mode === option}
                  onChange={() => {
                    change({ mode: option });
                  }}
                />
                {option === "folder"
                  ? "A folder on this server"
                  : "A Git remote"}
              </label>
            ))}
          </div>
        )}

        {mode === "folder" ? (
          <>
            <label className={LABEL} htmlFor={ids.folderPath}>
              Vault folder
            </label>
            <input
              id={ids.folderPath}
              className={INPUT}
              value={settings.folderPath}
              placeholder="/vaults/notes"
              onChange={(event) => {
                change({ folderPath: event.target.value });
              }}
            />
            <p className={HINT}>
              Mount it read-only; Tumnis never writes to the vault.
            </p>
          </>
        ) : (
          <>
            <label className={LABEL} htmlFor={ids.remote}>
              Git remote
            </label>
            <input
              id={ids.remote}
              className={INPUT}
              value={settings.remote}
              placeholder="ssh://git@git.example.com/notes.git"
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => {
                changeRemote(event.target.value);
              }}
            />
            <label className={LABEL} htmlFor={ids.branch}>
              Branch
            </label>
            <input
              id={ids.branch}
              className={INPUT}
              value={settings.branch}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => {
                change({ branch: event.target.value });
              }}
            />
            {deployKey && (
              <div className="flex min-w-0 flex-col gap-1">
                <p>
                  Add this key to the repository as a deploy key. Leave write
                  access off:
                </p>
                <code className="break-all font-mono text-sm">{deployKey}</code>
                <p className={HINT}>
                  A key that can write is refused when you connect.
                </p>
              </div>
            )}
            <button
              type="button"
              className={SECONDARY}
              disabled={busy || settings.remote.trim() === ""}
              onClick={() => {
                void run(async () => {
                  setConfirmed(null);
                  setProbed(await onProbeHostKey(settings.remote.trim()));
                });
              }}
            >
              Check the server&apos;s host key
            </button>
            {confirmed ? (
              <p role="status">Host key trusted: {probed?.sha256}</p>
            ) : (
              probed && (
                <HostKey
                  fingerprint={probed.sha256}
                  busy={busy}
                  onConfirm={() => {
                    setConfirmed(probed.knownHosts);
                    setPasted("");
                  }}
                />
              )
            )}
            {!confirmed && (
              <>
                <label className={LABEL} htmlFor={ids.pasted}>
                  Or paste the server&apos;s known_hosts line
                </label>
                <textarea
                  id={ids.pasted}
                  className={INPUT}
                  rows={2}
                  value={pasted}
                  autoComplete="off"
                  spellCheck={false}
                  onChange={(event) => {
                    setPasted(event.target.value);
                    setDone(false);
                  }}
                />
              </>
            )}
          </>
        )}
      </fieldset>

      <fieldset className={CARD}>
        <legend className="text-base font-semibold">
          Which project each note belongs to
        </legend>
        <p className={HINT}>
          A note&apos;s <code>tumnis_project</code> frontmatter key comes first,
          then a <code>#tumnis/&lt;project&gt;</code> tag, then the longest
          matching folder below.
        </p>
        {settings.folders.map((held, index) => (
          <div key={index} className="flex flex-wrap items-end gap-2">
            <label className={`${LABEL} min-w-0 flex-1`}>
              Folder
              <input
                className={INPUT}
                value={held.folder}
                placeholder="Clients/Acme"
                onChange={(event) => {
                  rule(index, { folder: event.target.value });
                }}
              />
            </label>
            <label className={`${LABEL} min-w-0 flex-1`}>
              Project
              <select
                className={INPUT}
                value={held.projectId}
                onChange={(event) => {
                  rule(index, { projectId: event.target.value });
                }}
              >
                <option value="">Choose a project</option>
                {projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className={SECONDARY}
              aria-label={`Remove folder rule ${String(index + 1)}`}
              onClick={() => {
                change({
                  folders: settings.folders.filter((_, at) => at !== index),
                });
              }}
            >
              Remove
            </button>
          </div>
        ))}
        <button
          type="button"
          className={SECONDARY}
          onClick={() => {
            change({
              folders: [...settings.folders, { folder: "", projectId: "" }],
            });
          }}
        >
          Add a folder rule
        </button>
        <label className={LABEL} htmlFor={ids.unmapped}>
          Notes that match no project
        </label>
        <select
          id={ids.unmapped}
          className={INPUT}
          value={settings.unmapped}
          onChange={(event) => {
            change({
              unmapped:
                event.target.value === "ignore" ? "ignore" : "workspace",
            });
          }}
        >
          <option value="workspace">Go to the workspace knowledge base</option>
          <option value="ignore">Are left out</option>
        </select>
        <label className={LABEL} htmlFor={ids.clippings}>
          Web clippings folder (its notes are untrusted)
        </label>
        <input
          id={ids.clippings}
          className={INPUT}
          value={settings.clippingsFolder}
          onChange={(event) => {
            change({ clippingsFolder: event.target.value });
          }}
        />
      </fieldset>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className={SECONDARY}
          disabled={busy || !sourceReady}
          onClick={() => {
            const asked: VaultConnect = { settings, knownHosts: hostsFor };
            void run(async () => {
              setPreview({ asked, rows: await onPreview(asked) });
            });
          }}
        >
          Preview the mapping
        </button>
        <button
          type="button"
          className={BUTTON}
          disabled={busy || !sourceReady || !previewCurrent}
          onClick={() => {
            void run(async () => {
              await onConnect({ settings, knownHosts: hostsFor });
              setDone(true);
            });
          }}
        >
          Connect
        </button>
      </div>
      {!previewCurrent && sourceReady && (
        <p className={HINT}>Preview the mapping before you connect.</p>
      )}
      {error && (
        <p role="alert" className={ERROR}>
          {error}
        </p>
      )}
      {done && <p role="status">Connected. The first sync has started.</p>}

      {preview && (
        <div
          className="max-w-full overflow-x-auto"
          role="region"
          aria-label="Mapping preview"
          tabIndex={0}
        >
          <table className={TABLE}>
            <thead className={TABLE_HEAD}>
              <tr>
                <th scope="col" className={TABLE_HEADER_CELL}>
                  Note
                </th>
                <th scope="col" className={TABLE_HEADER_CELL}>
                  Goes to
                </th>
                <th scope="col" className={TABLE_HEADER_CELL}>
                  Trust
                </th>
              </tr>
            </thead>
            <tbody className={TABLE_BODY}>
              {preview.rows.map((row) => (
                <tr key={row.path}>
                  <td className={`${TABLE_CELL} break-all`}>{row.path}</td>
                  <td className={TABLE_CELL}>
                    {row.ignored
                      ? "Left out"
                      : row.projectId
                        ? (names.get(row.projectId) ?? "A project")
                        : "Workspace knowledge base"}
                  </td>
                  <td className={TABLE_CELL}>
                    {row.untrusted ? (
                      <span className={badge("warning")}>Untrusted</span>
                    ) : (
                      <span className={badge("neutral")}>Trusted</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// API keys in Settings (P0-14, SEC-2, FR-9.3): create, copy once, rotate, revoke, last
// use. Mounted by the Settings screen (P0-26). The secret lives only in this component's
// state while its dialog is open: the mutations keep nothing (gcTime 0, reset on close)
// and never write it into the query cache; the list never carries it.
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { useId, useState, type SyntheticEvent } from "react";

import { apiFetch, problemDetail } from "../../lib/fetch";

// FR-14.10; the server refuses any other (422 `unknown_scope`).
export const SCOPES = [
  "tasks:read",
  "tasks:write",
  "context:read",
  "knowledge:write",
  "drafts:write",
  "delegate",
  "ingest",
] as const;

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  project_ids: string[] | null;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
}

interface ApiKeyCreated extends ApiKey {
  key: string;
}

const KEYS_QUERY = ["auth", "keys"] as const;

async function expectOk(response: Response): Promise<Response> {
  if (!response.ok) throw new Error(await problemDetail(response));
  return response;
}

async function listKeys(): Promise<ApiKey[]> {
  const response = await expectOk(await apiFetch("/v1/keys?limit=200"));
  const page = (await response.json()) as { items: ApiKey[] };
  return page.items;
}

async function createKey(body: {
  name: string;
  scopes: string[];
}): Promise<ApiKeyCreated> {
  const response = await expectOk(
    await apiFetch("/v1/keys", { method: "POST", body: JSON.stringify(body) }),
  );
  return (await response.json()) as ApiKeyCreated;
}

async function rotateKey(id: string): Promise<ApiKeyCreated> {
  const response = await expectOk(
    await apiFetch(`/v1/keys/${encodeURIComponent(id)}/rotate`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  );
  return (await response.json()) as ApiKeyCreated;
}

async function revokeKey(id: string): Promise<void> {
  await expectOk(
    await apiFetch(`/v1/keys/${encodeURIComponent(id)}`, { method: "DELETE" }),
  );
}

function when(value: string | null, none: string): string {
  return value === null ? none : new Date(value).toLocaleString();
}

function refresh(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: KEYS_QUERY });
}

function ShownOnce({
  secret,
  onClose,
}: {
  secret: string;
  onClose: () => void;
}) {
  const titleId = useId();
  const [copied, setCopied] = useState(false);
  return (
    <div role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <h3 id={titleId}>Copy your new key</h3>
      <p>It is shown only now. Store it somewhere safe before closing.</p>
      <code style={{ overflowWrap: "anywhere" }}>{secret}</code>
      <div>
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard.writeText(secret).then(() => {
              setCopied(true);
            });
          }}
        >
          Copy
        </button>
        <button type="button" onClick={onClose}>
          Done
        </button>
        {copied && <span role="status">Copied</span>}
      </div>
    </div>
  );
}

export function ApiKeys() {
  const queryClient = useQueryClient();
  const keys = useQuery({ queryKey: KEYS_QUERY, queryFn: listKeys });
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<ReadonlySet<string>>(new Set());
  const [secret, setSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const shown = {
    gcTime: 0,
    onSuccess: (created: ApiKeyCreated) => {
      setSecret(created.key);
      refresh(queryClient);
    },
    onError: (failure: Error) => {
      setError(failure.message);
    },
  };
  const create = useMutation({ mutationFn: createKey, ...shown });
  const rotate = useMutation({ mutationFn: rotateKey, ...shown });
  const revoke = useMutation({
    mutationFn: revokeKey,
    gcTime: 0,
    onSettled: () => {
      refresh(queryClient);
    },
    onError: (failure: Error) => {
      setError(failure.message);
    },
  });

  function toggle(scope: string) {
    setScopes((current) => {
      const next = new Set(current);
      if (next.has(scope)) next.delete(scope);
      else next.add(scope);
      return next;
    });
  }

  function submit(event: SyntheticEvent) {
    event.preventDefault();
    setError(null);
    create.mutate({ name: name.trim(), scopes: [...scopes].sort() });
    setName("");
    setScopes(new Set());
  }

  function close() {
    setSecret(null);
    create.reset();
    rotate.reset();
  }

  return (
    <section aria-labelledby="api-keys-title">
      <h2 id="api-keys-title">API keys</h2>
      <form onSubmit={submit}>
        <label>
          Key name
          <input
            required
            maxLength={200}
            value={name}
            onChange={(e) => {
              setName(e.target.value);
            }}
          />
        </label>
        <fieldset>
          <legend>Scopes</legend>
          {SCOPES.map((scope) => (
            <label key={scope}>
              <input
                type="checkbox"
                checked={scopes.has(scope)}
                onChange={() => {
                  toggle(scope);
                }}
              />
              {scope}
            </label>
          ))}
        </fieldset>
        <button type="submit" disabled={create.isPending}>
          Create key
        </button>
      </form>
      {error !== null && <p role="alert">{error}</p>}
      {secret !== null && <ShownOnce secret={secret} onClose={close} />}
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Prefix</th>
              <th scope="col">Scopes</th>
              <th scope="col">Last used</th>
              <th scope="col">Status</th>
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {(keys.data ?? []).map((key) => (
              <tr key={key.id}>
                <td>{key.name}</td>
                <td>
                  <code>{key.prefix}</code>
                </td>
                <td>{key.scopes.join(", ")}</td>
                <td>{when(key.last_used_at, "never")}</td>
                <td>
                  {key.revoked_at !== null
                    ? "revoked"
                    : key.expires_at === null
                      ? "active"
                      : `expires ${when(key.expires_at, "")}`}
                </td>
                <td>
                  {key.revoked_at === null && (
                    <>
                      <button
                        type="button"
                        disabled={rotate.isPending}
                        onClick={() => {
                          setError(null);
                          rotate.mutate(key.id);
                        }}
                      >
                        Rotate
                      </button>
                      <button
                        type="button"
                        disabled={revoke.isPending}
                        onClick={() => {
                          setError(null);
                          revoke.mutate(key.id);
                        }}
                      >
                        Revoke
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

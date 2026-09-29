// API keys in Settings (P0-14, SEC-2, FR-9.3): create, copy once, rotate, revoke, last
// use. Mounted by the Settings screen (P0-26). The secret lives only in this component's
// state while its dialog is open: the mutations keep nothing (gcTime 0, reset on close)
// and never write it into the query cache; the list never carries it.
import {
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { useId, useState, type SyntheticEvent } from "react";

import {
  authListKeysOptions,
  authListKeysQueryKey,
} from "../../api/@tanstack/react-query.gen";
import type { KeyCreated, KeyOut } from "../../api/types.gen";
import { apiWrite, useWrite } from "../../lib/fetch";

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

// The list query is the generated `authListKeys`, so a live `api_key` message (a key
// made, rotated or revoked in another tab) refreshes it (lib/live-map.ts).
const LIST = { query: { limit: 200 } };

function when(value: string | null | undefined, none: string): string {
  return value ? new Date(value).toLocaleString() : none;
}

function refresh(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: authListKeysQueryKey(LIST) });
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
  const keys = useQuery(authListKeysOptions(LIST));
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<ReadonlySet<string>>(new Set());
  const [secret, setSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const shown = {
    gcTime: 0,
    onSuccess: (created: KeyCreated) => {
      setSecret(created.key);
      refresh(queryClient);
    },
    onError: (failure: Error) => {
      setError(failure.message);
    },
  };
  const create = useWrite<
    { name: string; scopes: string[]; idempotencyKey?: string },
    KeyCreated
  >({
    mutationFn: ({ idempotencyKey, ...body }) =>
      apiWrite<KeyCreated>({
        kind: "create",
        method: "POST",
        path: "/keys",
        body,
        idempotencyKey,
      }),
    ...shown,
  });
  const rotate = useWrite<{ id: string; idempotencyKey?: string }, KeyCreated>({
    mutationFn: ({ id, idempotencyKey }) =>
      apiWrite<KeyCreated>({
        kind: "create",
        method: "POST",
        path: `/keys/${encodeURIComponent(id)}/rotate`,
        body: {},
        idempotencyKey,
      }),
    ...shown,
  });
  const revoke = useWrite<{ id: string; idempotencyKey?: string }, undefined>({
    mutationFn: ({ id, idempotencyKey }) =>
      apiWrite<undefined>({
        kind: "create",
        method: "DELETE",
        path: `/keys/${encodeURIComponent(id)}`,
        idempotencyKey,
      }),
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
            {(keys.data?.items ?? []).map((key: KeyOut) => (
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
                          rotate.mutate({ id: key.id });
                        }}
                      >
                        Rotate
                      </button>
                      <button
                        type="button"
                        disabled={revoke.isPending}
                        onClick={() => {
                          setError(null);
                          revoke.mutate({ id: key.id });
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

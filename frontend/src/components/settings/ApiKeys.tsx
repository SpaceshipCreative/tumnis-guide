// API keys in Settings (P0-14, SEC-2, FR-9.3): create, copy once, rotate, revoke, last
// use. Mounted by the Settings screen (KeysSection, P0-26), which asks before a revoke
// (`confirmRevoke`). The secret lives only in this component's state while its dialog is
// open: the mutations keep nothing (gcTime 0, reset on close) and never write it into the
// query cache; the list never carries it.
import {
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { useState, type SyntheticEvent } from "react";

import {
  authListKeysOptions,
  authListKeysQueryKey,
} from "../../api/@tanstack/react-query.gen";
import type { KeyCreated, KeyOut } from "../../api/types.gen";
import { apiWrite, useWrite } from "../../lib/fetch";
import { ConfirmDialog } from "./ConfirmDialog";
import { KeyCreatedDialog } from "./KeyCreatedDialog";
import {
  BUTTON,
  DANGER,
  HEADING,
  INPUT,
  LABEL,
  SECONDARY,
  SECTION,
} from "./styles";
import {
  TABLE,
  TABLE_BODY,
  TABLE_CELL,
  TABLE_HEAD,
  TABLE_HEADER_CELL,
} from "../common/ui";

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

export function ApiKeys({
  confirmRevoke = false,
}: {
  confirmRevoke?: boolean;
}) {
  const queryClient = useQueryClient();
  const keys = useQuery(authListKeysOptions(LIST));
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<ReadonlySet<string>>(new Set());
  const [secret, setSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<KeyOut | null>(null);

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
      setRevoking(null);
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

  function askRevoke(key: KeyOut) {
    setError(null);
    if (confirmRevoke) setRevoking(key);
    else revoke.mutate({ id: key.id });
  }

  return (
    <section aria-labelledby="api-keys-title" className={SECTION}>
      <h2 id="api-keys-title" className={HEADING}>
        API keys
      </h2>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className={LABEL}>
          Key name
          <input
            required
            maxLength={200}
            className={INPUT}
            value={name}
            onChange={(e) => {
              setName(e.target.value);
            }}
          />
        </label>
        <fieldset className="flex flex-col gap-1">
          <legend className="text-sm font-medium">Scopes</legend>
          <div className="flex flex-wrap gap-x-4">
            {SCOPES.map((scope) => (
              <label key={scope} className="flex min-h-11 items-center gap-2">
                <input
                  type="checkbox"
                  className="size-5"
                  checked={scopes.has(scope)}
                  onChange={() => {
                    toggle(scope);
                  }}
                />
                {scope}
              </label>
            ))}
          </div>
        </fieldset>
        <div>
          <button type="submit" className={BUTTON} disabled={create.isPending}>
            Create key
          </button>
        </div>
      </form>
      {error !== null && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      {secret !== null && <KeyCreatedDialog secret={secret} onClose={close} />}
      {revoking !== null && (
        <ConfirmDialog
          title="Revoke this key?"
          confirmLabel="Revoke key"
          busy={revoke.isPending}
          onCancel={() => {
            setRevoking(null);
          }}
          onConfirm={() => {
            revoke.mutate({ id: revoking.id });
          }}
        >
          <p>
            Anything using <strong>{revoking.name}</strong> stops working at
            once. This cannot be undone.
          </p>
        </ConfirmDialog>
      )}
      <div className="max-w-full overflow-x-auto rounded-xl border border-border bg-surface shadow-card">
        <table className={TABLE}>
          <thead className={TABLE_HEAD}>
            <tr>
              <th scope="col" className={TABLE_HEADER_CELL}>
                Name
              </th>
              <th scope="col" className={TABLE_HEADER_CELL}>
                Prefix
              </th>
              <th scope="col" className={TABLE_HEADER_CELL}>
                Scopes
              </th>
              <th scope="col" className={TABLE_HEADER_CELL}>
                Last used
              </th>
              <th scope="col" className={TABLE_HEADER_CELL}>
                Status
              </th>
              <th scope="col" className={TABLE_HEADER_CELL}>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody className={TABLE_BODY}>
            {(keys.data?.items ?? []).map((key: KeyOut) => (
              <tr key={key.id}>
                <td className={TABLE_CELL}>{key.name}</td>
                <td className={TABLE_CELL}>
                  <code>{key.prefix}</code>
                </td>
                <td className={TABLE_CELL}>{key.scopes.join(", ")}</td>
                <td className={TABLE_CELL}>
                  {when(key.last_used_at, "never")}
                </td>
                <td className={TABLE_CELL}>
                  {key.revoked_at !== null
                    ? "revoked"
                    : key.expires_at === null
                      ? "active"
                      : `expires ${when(key.expires_at, "")}`}
                </td>
                <td className={TABLE_CELL}>
                  {key.revoked_at === null && (
                    <div className="flex gap-2">
                      <button
                        type="button"
                        className={SECONDARY}
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
                        className={DANGER}
                        disabled={revoke.isPending}
                        onClick={() => {
                          askRevoke(key);
                        }}
                      >
                        Revoke
                      </button>
                    </div>
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

// Settings > Storage (P1-14, FR-15.7): where knowledge-base files live. Lists the
// workspace's locations with their status in plain words, adds a server folder or an S3
// bucket (the keys go to the server once and never come back), tests a connection and
// makes a location the default for new projects.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type SyntheticEvent, useId, useState } from "react";

import type { LocationOut } from "../../api/types.gen";
import { ApiError, apiWrite, ConflictError, useWrite } from "../../lib/fetch";
import { storageQuery } from "./queries";
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
} from "./styles";
import { badge } from "../common/ui";

type Kind = "server_path" | "s3";

const KIND_LABELS: Record<Kind, string> = {
  server_path: "Folder on the server",
  s3: "S3 bucket",
};

// Why a location is offline, as the Settings screen says it (status_reason codes).
const OFFLINE_REASONS: Record<string, string> = {
  marker_missing: "Folder offline: marker file missing",
  bucket_missing: "Bucket offline: bucket not found",
  access_denied: "Offline: the keys were refused",
  unreachable: "Offline: the endpoint does not answer",
  ssrf_blocked: "Offline: that endpoint is not allowed",
};

const FIXES: Record<string, string> = {
  marker_missing:
    "Mount the share, then check that the file .tumnis-root is in its top folder.",
};

function statusText(location: LocationOut): string {
  if (location.status === "online") return "Online";
  return OFFLINE_REASONS[location.status_reason ?? ""] ?? "Offline";
}

function isKind(value: string): value is Kind {
  return value === "server_path" || value === "s3";
}

function problemText(error: Error): string {
  if (error instanceof ConflictError) {
    return error.problem.code === "stale_version"
      ? "That location changed elsewhere; the list is up to date now."
      : (error.problem.detail ?? error.message);
  }
  return error instanceof ApiError
    ? (error.problem.detail ?? error.message)
    : "That did not work.";
}

interface Draft {
  name: string;
  kind: Kind;
  root: string;
  endpoint: string;
  region: string;
  accessKey: string;
  secretKey: string;
}

const EMPTY: Draft = {
  name: "",
  kind: "server_path",
  root: "",
  endpoint: "",
  region: "",
  accessKey: "",
  secretKey: "",
};

function requestBody(draft: Draft): Record<string, unknown> {
  const base = {
    name: draft.name.trim(),
    kind: draft.kind,
    root: draft.root.trim(),
    is_default: false,
  };
  if (draft.kind === "server_path") return base;
  return {
    ...base,
    s3: {
      endpoint: draft.endpoint.trim(),
      region: draft.region.trim() || "us-east-1",
      access_key: draft.accessKey.trim(),
      secret_key: draft.secretKey,
    },
  };
}

export function StorageSection() {
  const queryClient = useQueryClient();
  const ids = {
    name: useId(),
    kind: useId(),
    root: useId(),
    endpoint: useId(),
    region: useId(),
    accessKey: useId(),
    secretKey: useId(),
  };
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [message, setMessage] = useState<string | null>(null);
  const list = useQuery(storageQuery());

  const refresh = () =>
    queryClient.invalidateQueries({
      predicate: (query) =>
        (query.queryKey[0] as { _id?: string } | undefined)?._id ===
        "knowledgeListLocations",
    });

  const add = useWrite<
    { body: Record<string, unknown>; idempotencyKey?: string },
    LocationOut
  >({
    mutationFn: ({ body, idempotencyKey }) =>
      apiWrite<LocationOut>({
        kind: "create",
        method: "POST",
        path: "/knowledge/locations",
        body,
        idempotencyKey,
      }),
    onSuccess: (location) => {
      setDraft(EMPTY);
      setMessage(`Added ${location.name}: ${statusText(location)}.`);
    },
    onError: (error) => {
      setMessage(problemText(error));
    },
    onSettled: () => void refresh(),
  });

  const act = useWrite<
    {
      action: "test" | "default";
      location: LocationOut;
      idempotencyKey?: string;
    },
    LocationOut
  >({
    mutationFn: ({ action, location, idempotencyKey }) => {
      const path = `/knowledge/locations/${encodeURIComponent(location.id)}/${action}`;
      return action === "default"
        ? apiWrite<LocationOut>({
            kind: "update",
            method: "POST",
            path,
            body: {},
            version: location.version,
            idempotencyKey,
          })
        : apiWrite<LocationOut>({
            kind: "create",
            method: "POST",
            path,
            body: {},
            idempotencyKey,
          });
    },
    onSuccess: (location, { action }) => {
      setMessage(
        action === "default"
          ? `${location.name} is the default for new projects.`
          : `${location.name}: ${statusText(location)}.`,
      );
    },
    onError: (error) => {
      setMessage(problemText(error));
    },
    onSettled: () => void refresh(),
  });

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    setMessage(null);
    add.mutate({ body: requestBody(draft) });
    setDraft((current) => ({ ...current, secretKey: "" }));
  };

  const set =
    (field: keyof Draft) =>
    (event: { target: { value: string } }): void => {
      const value = event.target.value;
      setDraft((current) => ({ ...current, [field]: value }));
    };

  const locations = list.data ?? [];
  const s3 = draft.kind === "s3";
  return (
    <section aria-labelledby="storage-title" className={SECTION}>
      <h2 id="storage-title" className={HEADING}>
        Storage
      </h2>
      <p className={HINT}>
        Where project files and notes are kept. New projects get a folder on the
        default location.
      </p>
      <p role="status" className={HINT}>
        {message}
      </p>
      {list.isError && (
        <p role="alert" className={ERROR}>
          The storage locations could not be loaded.
        </p>
      )}
      {list.isSuccess && locations.length === 0 && (
        <p className={HINT}>No storage locations yet.</p>
      )}
      <ul className="flex flex-col gap-2">
        {locations.map((location) => (
          <li key={location.id} className={CARD}>
            <div className="flex flex-wrap items-center gap-2">
              <p className="font-medium [overflow-wrap:anywhere]">
                {location.name}
              </p>
              {location.is_default && (
                <span className={badge("accent")}>Default</span>
              )}
            </div>
            <p className="text-sm [overflow-wrap:anywhere]">
              {isKind(location.kind)
                ? KIND_LABELS[location.kind]
                : location.kind}
              {": "}
              {location.root}
            </p>
            <p
              className={
                location.status === "online" ? HINT : "text-sm text-danger"
              }
            >
              {statusText(location)}
            </p>
            {location.status === "offline" &&
              FIXES[location.status_reason ?? ""] !== undefined && (
                <p className={HINT}>{FIXES[location.status_reason ?? ""]}</p>
              )}
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className={SECONDARY}
                disabled={act.isPending}
                onClick={() => {
                  setMessage(null);
                  act.mutate({ action: "test", location });
                }}
              >
                Test connection
              </button>
              {!location.is_default && (
                <button
                  type="button"
                  className={SECONDARY}
                  disabled={act.isPending}
                  onClick={() => {
                    setMessage(null);
                    act.mutate({ action: "default", location });
                  }}
                >
                  Make default
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
      <form className="flex max-w-lg flex-col gap-3" onSubmit={submit}>
        <h3 className="font-semibold">Add a location</h3>
        <div className="flex flex-col gap-1">
          <label htmlFor={ids.name} className={LABEL}>
            Name
          </label>
          <input
            id={ids.name}
            className={INPUT}
            required
            maxLength={100}
            value={draft.name}
            onChange={set("name")}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={ids.kind} className={LABEL}>
            Kind
          </label>
          <select
            id={ids.kind}
            className={INPUT}
            value={draft.kind}
            onChange={(event) => {
              const value = event.target.value;
              if (isKind(value))
                setDraft((current) => ({ ...current, kind: value }));
            }}
          >
            {(Object.keys(KIND_LABELS) as Kind[]).map((kind) => (
              <option key={kind} value={kind}>
                {KIND_LABELS[kind]}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={ids.root} className={LABEL}>
            {s3 ? "Bucket and prefix" : "Folder path"}
          </label>
          <input
            id={ids.root}
            className={INPUT}
            required
            placeholder={s3 ? "bucket/tumnis" : "/srv/tumnis"}
            value={draft.root}
            onChange={set("root")}
          />
        </div>
        {s3 && (
          <>
            <div className="flex flex-col gap-1">
              <label htmlFor={ids.endpoint} className={LABEL}>
                Endpoint
              </label>
              <input
                id={ids.endpoint}
                className={INPUT}
                type="url"
                required
                placeholder="https://s3.example.com"
                value={draft.endpoint}
                onChange={set("endpoint")}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor={ids.region} className={LABEL}>
                Region
              </label>
              <input
                id={ids.region}
                className={INPUT}
                placeholder="us-east-1"
                autoComplete="off"
                value={draft.region}
                onChange={set("region")}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor={ids.accessKey} className={LABEL}>
                Access key
              </label>
              <input
                id={ids.accessKey}
                className={INPUT}
                required
                autoComplete="off"
                value={draft.accessKey}
                onChange={set("accessKey")}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor={ids.secretKey} className={LABEL}>
                Secret key
              </label>
              <input
                id={ids.secretKey}
                className={INPUT}
                type="password"
                required
                autoComplete="new-password"
                value={draft.secretKey}
                onChange={set("secretKey")}
              />
            </div>
          </>
        )}
        <div>
          <button type="submit" className={BUTTON} disabled={add.isPending}>
            Add location
          </button>
        </div>
      </form>
    </section>
  );
}

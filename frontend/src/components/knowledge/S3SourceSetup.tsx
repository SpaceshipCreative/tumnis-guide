// Settings > Linked sources: S3 buckets (P3-13, FR-15.11). Documents already in a bucket
// sync into a project's knowledge base without re-uploading. The form takes the endpoint,
// region, bucket, keys, path-style addressing, whether the files are trusted, and which
// prefix goes to which project. The keys must be read-only: MinIO and Backblaze B2 keys are
// checked and a key that can write is refused; other providers are accepted with a
// warning. The MinIO webhook token comes back once, with the commands to set it up.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type SyntheticEvent, useId, useState } from "react";

import {
  knowledgeListS3SourcesOptions,
  projectsListProjectsOptions,
} from "../../api/@tanstack/react-query.gen";
import type { S3SourceCreated, S3SourceOut } from "../../api/types.gen";
import { ApiError, apiWrite, useWrite } from "../../lib/fetch";
import { badge } from "../common/ui";
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

type Provider = "minio" | "b2" | "other";

const PROVIDERS: Record<Provider, string> = {
  minio: "MinIO",
  b2: "Backblaze B2",
  other: "Other S3-compatible (AWS S3 and others)",
};

const PROBLEMS: Record<string, string> = {
  key_not_read_only:
    "That key can write or delete, or reaches other buckets. Make a read-only key for this bucket.",
  b2_master_key: "Use a B2 application key, never the account's master key.",
  ssrf_blocked: "That endpoint is not allowed.",
  capability_check_failed:
    "The provider did not let Tumnis check the key. Check it, or choose Other to connect without the check.",
};

let nextRow = 0;

/** A fresh row with its own key (rows are added and edited, never reordered). */
function newRow(): PrefixRow {
  nextRow += 1;
  return { key: `row-${String(nextRow)}`, prefix: "", projectId: "" };
}

interface PrefixRow {
  key: string;
  prefix: string;
  projectId: string; // "" = the workspace knowledge base
}

interface Draft {
  provider: Provider;
  endpoint: string;
  region: string;
  bucket: string;
  accessKey: string;
  secretKey: string;
  pathStyle: boolean;
  trusted: boolean;
  prefixes: PrefixRow[];
}

const EMPTY: Draft = {
  provider: "minio",
  endpoint: "",
  region: "us-east-1",
  bucket: "",
  accessKey: "",
  secretKey: "",
  pathStyle: true,
  trusted: false,
  prefixes: [],
};

function emptyDraft(): Draft {
  return { ...EMPTY, prefixes: [newRow()] };
}

function isProvider(value: string): value is Provider {
  return Object.hasOwn(PROVIDERS, value);
}

function problemText(error: Error): string {
  if (error instanceof ApiError) {
    return (
      PROBLEMS[error.problem.code] ?? error.problem.detail ?? error.message
    );
  }
  return "That did not work.";
}

function requestBody(draft: Draft): Record<string, unknown> {
  return {
    provider: draft.provider,
    endpoint: draft.endpoint.trim(),
    region: draft.region.trim() || "us-east-1",
    bucket: draft.bucket.trim(),
    access_key: draft.accessKey.trim(),
    secret_key: draft.secretKey,
    path_style: draft.pathStyle,
    trusted: draft.trusted,
    prefixes: draft.prefixes.map((row) => ({
      prefix: row.prefix.trim(),
      project_id: row.projectId || null,
    })),
  };
}

function Created({ source }: { source: S3SourceCreated }) {
  return (
    <div className={CARD} role="region" aria-label="Webhook token">
      <p className="font-medium">
        Copy this token now: Tumnis shows it only once.
      </p>
      <code className="text-sm [overflow-wrap:anywhere]">
        {source.webhook_token}
      </code>
      {source.provider === "minio" && (
        <>
          <p className={HINT}>
            Optional: run these with the MinIO client (replace ALIAS with your
            alias) so changes arrive within seconds. Without them Tumnis still
            checks the bucket every 15 minutes.
          </p>
          <ul className="flex flex-col gap-1">
            {source.minio_commands.map((command) => (
              <li key={command}>
                <code className="text-sm [overflow-wrap:anywhere]">
                  {command}
                </code>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function SourceCard({ source }: { source: S3SourceOut }) {
  return (
    <li className={CARD}>
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium [overflow-wrap:anywhere]">{source.bucket}</p>
        <span className={badge(source.trusted ? "accent" : "neutral")}>
          {source.trusted ? "Trusted" : "Untrusted"}
        </span>
      </div>
      <p className="text-sm [overflow-wrap:anywhere]">
        {isProvider(source.provider)
          ? PROVIDERS[source.provider]
          : source.provider}
        {": "}
        {source.endpoint}
      </p>
      <p className="text-sm [overflow-wrap:anywhere]">
        Prefixes:{" "}
        {source.prefixes.map((p) => p.prefix || "(whole bucket)").join(", ")}
      </p>
      {source.warning && (
        <p className="text-sm text-danger">{source.warning}</p>
      )}
    </li>
  );
}

export function S3SourceSetup() {
  const queryClient = useQueryClient();
  const ids = {
    provider: useId(),
    endpoint: useId(),
    region: useId(),
    bucket: useId(),
    accessKey: useId(),
    secretKey: useId(),
    pathStyle: useId(),
    trusted: useId(),
  };
  const prefixId = useId();
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [message, setMessage] = useState<string | null>(null);
  const [created, setCreated] = useState<S3SourceCreated | null>(null);
  const list = useQuery(knowledgeListS3SourcesOptions());
  const projects = useQuery(projectsListProjectsOptions());

  const add = useWrite<
    { body: Record<string, unknown>; idempotencyKey?: string },
    S3SourceCreated
  >({
    mutationFn: ({ body, idempotencyKey }) =>
      apiWrite<S3SourceCreated>({
        kind: "create",
        method: "POST",
        path: "/knowledge/s3-sources",
        body,
        idempotencyKey,
      }),
    onSuccess: (source) => {
      setDraft(emptyDraft());
      setCreated(source);
      setMessage(
        source.warning
          ? `Linked ${source.bucket}. ${source.warning}.`
          : `Linked ${source.bucket}: the key is read-only.`,
      );
    },
    onError: (error) => {
      setMessage(problemText(error));
    },
    onSettled: () =>
      void queryClient.invalidateQueries({
        predicate: (query) =>
          (query.queryKey[0] as { _id?: string } | undefined)?._id ===
          "knowledgeListS3Sources",
      }),
  });

  const set =
    (
      field:
        | "provider"
        | "endpoint"
        | "region"
        | "bucket"
        | "accessKey"
        | "secretKey",
    ) =>
    (event: { target: { value: string } }): void => {
      const value = event.target.value;
      setDraft((current) => ({ ...current, [field]: value }));
    };

  const setPrefix = (index: number, change: Partial<PrefixRow>) => {
    setDraft((current) => ({
      ...current,
      prefixes: current.prefixes.map((row, at) =>
        at === index ? { ...row, ...change } : row,
      ),
    }));
  };

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    setMessage(null);
    setCreated(null);
    add.mutate({ body: requestBody(draft) });
    setDraft((current) => ({ ...current, secretKey: "" }));
  };

  const sources = list.data ?? [];
  const projectRows = projects.data?.items ?? [];
  return (
    <section aria-labelledby="s3-sources-title" className={SECTION}>
      <h2 id="s3-sources-title" className={HEADING}>
        S3 buckets
      </h2>
      <p className={HINT}>
        Link a bucket whose documents should feed a project's knowledge base.
        Tumnis only reads it, so give it a read-only key.
      </p>
      <p role="status" className={HINT}>
        {message}
      </p>
      {created && <Created source={created} />}
      {list.isError && (
        <p role="alert" className={ERROR}>
          The linked buckets could not be loaded.
        </p>
      )}
      {list.isSuccess && sources.length === 0 && (
        <p className={HINT}>No linked buckets yet.</p>
      )}
      <ul className="flex flex-col gap-2">
        {sources.map((source) => (
          <SourceCard key={source.id} source={source} />
        ))}
      </ul>
      <form className={CARD} onSubmit={submit}>
        <div className="flex flex-col gap-1">
          <label htmlFor={ids.provider} className={LABEL}>
            Provider
          </label>
          <select
            id={ids.provider}
            className={INPUT}
            value={draft.provider}
            onChange={set("provider")}
          >
            {Object.entries(PROVIDERS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          {draft.provider === "other" && (
            <p className={HINT}>
              Tumnis cannot check these keys: make sure yours is read-only.
            </p>
          )}
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={ids.endpoint} className={LABEL}>
            Endpoint
          </label>
          <input
            id={ids.endpoint}
            className={INPUT}
            required
            inputMode="url"
            placeholder={
              draft.provider === "b2"
                ? "https://s3.<region>.backblazeb2.com"
                : "https://minio.example.lan:9000"
            }
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
            value={draft.region}
            onChange={set("region")}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={ids.bucket} className={LABEL}>
            Bucket
          </label>
          <input
            id={ids.bucket}
            className={INPUT}
            required
            value={draft.bucket}
            onChange={set("bucket")}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={ids.accessKey} className={LABEL}>
            {draft.provider === "b2" ? "Application key ID" : "Access key"}
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
            {draft.provider === "b2" ? "Application key" : "Secret key"}
          </label>
          <input
            id={ids.secretKey}
            className={INPUT}
            required
            type="password"
            autoComplete="off"
            value={draft.secretKey}
            onChange={set("secretKey")}
          />
        </div>
        <div className="flex min-h-11 items-center gap-2">
          <input
            id={ids.pathStyle}
            type="checkbox"
            className="size-5"
            checked={draft.pathStyle}
            onChange={(event) => {
              const checked = event.target.checked;
              setDraft((current) => ({ ...current, pathStyle: checked }));
            }}
          />
          <label htmlFor={ids.pathStyle} className={LABEL}>
            Path-style addressing (MinIO and most self-hosted endpoints)
          </label>
        </div>
        <div className="flex min-h-11 items-center gap-2">
          <input
            id={ids.trusted}
            type="checkbox"
            className="size-5"
            checked={draft.trusted}
            onChange={(event) => {
              const checked = event.target.checked;
              setDraft((current) => ({ ...current, trusted: checked }));
            }}
          />
          <label htmlFor={ids.trusted} className={LABEL}>
            Trust these files (only if no one outside can add to the bucket)
          </label>
        </div>
        <fieldset className="flex min-w-0 flex-col gap-2">
          <legend className={LABEL}>Prefixes and projects</legend>
          {draft.prefixes.map((row, index) => (
            <div key={row.key} className="flex min-w-0 flex-wrap gap-2">
              <label
                htmlFor={`${prefixId}-p${String(index)}`}
                className="sr-only"
              >
                Prefix {index + 1}
              </label>
              <input
                id={`${prefixId}-p${String(index)}`}
                className={`${INPUT} min-w-0 flex-1`}
                placeholder="clients/acme/"
                value={row.prefix}
                onChange={(event) => {
                  setPrefix(index, { prefix: event.target.value });
                }}
              />
              <label
                htmlFor={`${prefixId}-j${String(index)}`}
                className="sr-only"
              >
                Project for prefix {index + 1}
              </label>
              <select
                id={`${prefixId}-j${String(index)}`}
                className={`${INPUT} min-w-0 flex-1`}
                value={row.projectId}
                onChange={(event) => {
                  setPrefix(index, { projectId: event.target.value });
                }}
              >
                <option value="">Workspace knowledge base</option>
                {projectRows.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </select>
            </div>
          ))}
          <div>
            <button
              type="button"
              className={SECONDARY}
              onClick={() => {
                setDraft((current) => ({
                  ...current,
                  prefixes: [...current.prefixes, newRow()],
                }));
              }}
            >
              Add a prefix
            </button>
          </div>
        </fieldset>
        <div>
          <button type="submit" className={BUTTON} disabled={add.isPending}>
            Link bucket
          </button>
        </div>
      </form>
    </section>
  );
}

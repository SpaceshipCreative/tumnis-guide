// Knowledge (P1-17, FR-15.1, FR-15.6): the project's knowledge items in the Context rail.
// Its one-line summary gives the item count and the space the workspace uses against
// its quota; open, it adds a text entry, an upload or a link, pins an item (with the
// version read) and moves one to the trash with Undo. A text entry opens in the note
// editor, which loads only then (LazyEditor), so Tiptap stays out of the initial bundle.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  knowledgeGetQuotaOptions,
  knowledgeGetQuotaQueryKey,
  knowledgeListDocumentsOptions,
  knowledgeListDocumentsQueryKey,
} from "../../../api/@tanstack/react-query.gen";
import type { DocumentDto } from "../../../api/types.gen";
import { zDocumentDto, zUploadAccepted } from "../../../api/zod.gen";
import { BUTTON_QUIET, ERROR_TEXT, FIELD_LABEL, HINT } from "../../common/ui";
import { apiUpload, apiWrite } from "../../../lib/fetch";
import { LazyEditor } from "../../../editor/LazyEditor";
import { fieldClass, RailSection, saveClass } from "./RailSection";

const LIST_LIMIT = 100;
const UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

/** Bytes for people: 1024-based, at most one decimal, no trailing `.0` ("1.5 MB", "10 GB"). */
export function formatBytes(bytes: number): string {
  let value = Math.max(bytes, 0);
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const shown =
    unit === 0
      ? String(Math.round(value))
      : String(Math.round(value * 10) / 10);
  return `${shown} ${UNITS[unit] ?? "B"}`;
}

export function knowledgeSummary(
  count: number,
  usedBytes: number,
  quotaBytes: number,
): string {
  const items = `${String(count)} ${count === 1 ? "item" : "items"}`;
  return `${items} · ${formatBytes(usedBytes)} of ${formatBytes(quotaBytes)}`;
}

type Adding = "text" | "link" | null;

function AddText({
  projectId,
  onDone,
}: {
  projectId: string;
  onDone: () => void;
}) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        setBusy(true);
        setFailed(false);
        apiWrite({
          kind: "create",
          method: "POST",
          path: "/knowledge/documents/text",
          body: { project_id: projectId, title: title.trim(), body_md: text },
          idempotencyKey: crypto.randomUUID(),
          schema: zDocumentDto,
        }).then(onDone, () => {
          setFailed(true);
          setBusy(false);
        });
      }}
    >
      <label className={FIELD_LABEL}>
        Title
        <input
          value={title}
          required
          onChange={(event) => {
            setTitle(event.target.value);
          }}
          className={fieldClass}
        />
      </label>
      <label className={FIELD_LABEL}>
        Text
        <textarea
          rows={4}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
          }}
          className={fieldClass}
        />
      </label>
      <button
        type="submit"
        disabled={busy || title.trim() === ""}
        className={saveClass}
      >
        Add
      </button>
      {failed && (
        <p className={ERROR_TEXT}>Could not add the text. Try again.</p>
      )}
    </form>
  );
}

function AddLink({
  projectId,
  onDone,
}: {
  projectId: string;
  onDone: () => void;
}) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        setBusy(true);
        setFailed(false);
        apiWrite({
          kind: "create",
          method: "POST",
          path: "/knowledge/documents/link",
          body: { project_id: projectId, url: url.trim() },
          idempotencyKey: crypto.randomUUID(),
          schema: zDocumentDto,
        }).then(onDone, () => {
          setFailed(true);
          setBusy(false);
        });
      }}
    >
      <label className={FIELD_LABEL}>
        Link
        <input
          type="url"
          inputMode="url"
          value={url}
          required
          onChange={(event) => {
            setUrl(event.target.value);
          }}
          className={fieldClass}
        />
      </label>
      <button
        type="submit"
        disabled={busy || url.trim() === ""}
        className={saveClass}
      >
        Add
      </button>
      {failed && (
        <p className={ERROR_TEXT}>
          Could not add the link. Check it and try again.
        </p>
      )}
    </form>
  );
}

function ItemRow({
  doc,
  open,
  onOpen,
  onPin,
  onTrash,
}: {
  doc: DocumentDto;
  open: boolean;
  onOpen: () => void;
  onPin: () => void;
  onTrash: () => void;
}) {
  const canEdit = doc.kind === "text" && doc.status === "ready";
  return (
    <li className="flex flex-col gap-1">
      <div className="flex min-h-11 items-center gap-1 md:min-h-8">
        {canEdit ? (
          <button
            type="button"
            aria-expanded={open}
            onClick={onOpen}
            className="min-w-0 flex-1 truncate rounded px-2 text-left text-sm hover:bg-surface-muted"
          >
            {doc.title}
          </button>
        ) : doc.kind === "link" && doc.provider_url ? (
          <a
            href={doc.provider_url}
            target="_blank"
            rel="noopener noreferrer"
            className="min-w-0 flex-1 truncate px-2 text-sm text-accent"
          >
            {doc.title}
          </a>
        ) : (
          <span className="min-w-0 flex-1 truncate px-2 text-sm">
            {doc.title}
          </span>
        )}
        <button
          type="button"
          aria-pressed={doc.pinned}
          aria-label={`${doc.pinned ? "Unpin" : "Pin"} ${doc.title}`}
          onClick={onPin}
          className={BUTTON_QUIET}
        >
          {doc.pinned ? "Unpin" : "Pin"}
        </button>
        <button
          type="button"
          aria-label={`Move ${doc.title} to trash`}
          onClick={onTrash}
          className={BUTTON_QUIET}
        >
          Trash
        </button>
      </div>
      {open && canEdit && (
        <LazyEditor
          key={`${doc.id}:${String(doc.version)}`}
          document={doc}
          projectId={doc.project_id}
        />
      )}
    </li>
  );
}

export function KnowledgeSection({
  projectId,
  open,
  onToggle,
}: {
  projectId: string;
  open: boolean;
  onToggle: () => void;
}) {
  const client = useQueryClient();
  const listArgs = { query: { project_id: projectId, limit: LIST_LIMIT } };
  const quotaArgs = { query: { project_id: projectId } };
  const docs = useQuery(knowledgeListDocumentsOptions(listArgs));
  const quota = useQuery(knowledgeGetQuotaOptions(quotaArgs));
  const [adding, setAdding] = useState<Adding>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [trashed, setTrashed] = useState<DocumentDto | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({
        queryKey: knowledgeListDocumentsQueryKey(listArgs),
      }),
      client.invalidateQueries({
        queryKey: knowledgeGetQuotaQueryKey(quotaArgs),
      }),
    ]);
  };
  const run = (work: Promise<unknown>, failure: string) => {
    setProblem(null);
    work.then(refresh, () => {
      setProblem(failure);
    });
  };

  // The brief is a knowledge document too, with its own section above: it is neither
  // listed nor counted here (the quota's count includes it).
  const listed = docs.data?.items ?? [];
  const items = listed.filter((d) => d.role !== "brief");
  const briefs = listed.length - items.length;
  const summary =
    quota.data !== undefined
      ? knowledgeSummary(
          Math.max(quota.data.count - briefs, 0),
          quota.data.used_bytes,
          quota.data.quota_bytes,
        )
      : quota.isError
        ? "Knowledge could not be loaded"
        : "Loading…";

  const upload = (file: File) => {
    const form = new FormData();
    form.append("file", file);
    form.append("project_id", projectId);
    run(
      apiUpload({
        path: "/knowledge/documents",
        form,
        idempotencyKey: crypto.randomUUID(),
        schema: zUploadAccepted,
      }),
      `Could not upload ${file.name}.`,
    );
  };

  return (
    <RailSection
      title="Knowledge"
      summary={summary}
      open={open}
      onToggle={onToggle}
    >
      {items.length === 0 && !docs.isPending ? (
        <p className={HINT}>No items yet. Add text, a file or a link.</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {items.map((doc) => (
            <ItemRow
              key={doc.id}
              doc={doc}
              open={editing === doc.id}
              onOpen={() => {
                setEditing((current) => (current === doc.id ? null : doc.id));
              }}
              onPin={() => {
                run(
                  apiWrite({
                    kind: "update",
                    method: "PATCH",
                    path: `/knowledge/documents/${doc.id}`,
                    body: { pinned: !doc.pinned },
                    version: doc.version,
                    idempotencyKey: crypto.randomUUID(),
                    schema: zDocumentDto,
                  }),
                  `Could not ${doc.pinned ? "unpin" : "pin"} ${doc.title}.`,
                );
              }}
              onTrash={() => {
                setTrashed(doc);
                run(
                  apiWrite({
                    kind: "create",
                    method: "DELETE",
                    path: `/knowledge/documents/${doc.id}`,
                    idempotencyKey: crypto.randomUUID(),
                  }),
                  `Could not move ${doc.title} to the trash.`,
                );
              }}
            />
          ))}
        </ul>
      )}
      {trashed && (
        <div role="status" className="flex items-center gap-2 text-sm">
          <span className="min-w-0 flex-1 truncate">
            {trashed.title} moved to trash.
          </span>
          <button
            type="button"
            className={BUTTON_QUIET}
            onClick={() => {
              const doc = trashed;
              setTrashed(null);
              run(
                apiWrite({
                  kind: "create",
                  method: "POST",
                  path: `/knowledge/documents/${doc.id}/restore`,
                  idempotencyKey: crypto.randomUUID(),
                  schema: zDocumentDto,
                }),
                `Could not restore ${doc.title}.`,
              );
            }}
          >
            Undo
          </button>
        </div>
      )}
      {problem && <p className={ERROR_TEXT}>{problem}</p>}
      <div className="flex flex-wrap items-center gap-1">
        <button
          type="button"
          aria-expanded={adding === "text"}
          onClick={() => {
            setAdding((a) => (a === "text" ? null : "text"));
          }}
          className={BUTTON_QUIET}
        >
          Add text
        </button>
        <button
          type="button"
          aria-expanded={adding === "link"}
          onClick={() => {
            setAdding((a) => (a === "link" ? null : "link"));
          }}
          className={BUTTON_QUIET}
        >
          Add link
        </button>
        <label
          className={`${BUTTON_QUIET} cursor-pointer focus-within:outline-2`}
        >
          Upload a file
          <input
            type="file"
            className="sr-only"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) upload(file);
            }}
          />
        </label>
      </div>
      {adding === "text" && (
        <AddText
          projectId={projectId}
          onDone={() => {
            setAdding(null);
            void refresh();
          }}
        />
      )}
      {adding === "link" && (
        <AddLink
          projectId={projectId}
          onDone={() => {
            setAdding(null);
            void refresh();
          }}
        />
      )}
    </RailSection>
  );
}

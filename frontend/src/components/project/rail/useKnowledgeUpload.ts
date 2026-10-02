// A file upload to a project's knowledge (P1-16, P1-17, FR-15.2): `POST
// /v1/knowledge/documents` (multipart), shared by the Knowledge section's "Upload a file"
// and a file dropped on the composer. The answer (202, `pending_scan`) goes into the
// project's knowledge list at once, so the item shows "Scanning" before the extract
// worker has picked it up; the list then polls until every item has settled.
import { type QueryClient, useQueryClient } from "@tanstack/react-query";

import {
  knowledgeGetQuotaQueryKey,
  knowledgeListDocumentsOptions,
} from "../../../api/@tanstack/react-query.gen";
import type {
  DocumentDto,
  PageDocumentDto,
  UploadAccepted,
} from "../../../api/types.gen";
import { zUploadAccepted } from "../../../api/zod.gen";
import { apiUpload } from "../../../lib/fetch";

const LIST_LIMIT = 100;
const POLL_MS = 1_000;

/** The arguments of the project's knowledge list (the Knowledge section's query). */
export function knowledgeListArgs(projectId: string) {
  return { query: { project_id: projectId, limit: LIST_LIMIT } };
}

export function knowledgeQuotaArgs(projectId: string) {
  return { query: { project_id: projectId } };
}

/** A document still on its way through the scan and extraction pipeline. */
export function isSettling(doc: Pick<DocumentDto, "status">): boolean {
  return doc.status === "pending_scan" || doc.status === "extracting";
}

/** How often the knowledge list refetches: every second while an item is settling. */
export function knowledgePollInterval(
  page: PageDocumentDto | undefined,
): number | false {
  return page?.items.some(isSettling) ? POLL_MS : false;
}

/** The list row for an upload the server has just accepted (an untrusted file). */
export function acceptedDocument(
  accepted: UploadAccepted,
  file: File,
  projectId: string,
): DocumentDto {
  return {
    id: accepted.id,
    current_version_id: accepted.version_id,
    status: accepted.status,
    status_reason: null,
    title: file.name,
    kind: "file",
    source: "upload",
    role: null,
    label: null,
    path: null,
    pinned: false,
    project_id: projectId,
    provider_url: null,
    body_md: null,
    tags: [],
    tainted: true,
    trust: "untrusted",
    version: 1,
  };
}

async function showAccepted(
  client: QueryClient,
  projectId: string,
  doc: DocumentDto,
): Promise<void> {
  const options = knowledgeListDocumentsOptions(knowledgeListArgs(projectId));
  // The list may not be loaded yet (the phone's Context sheet is closed): load it first,
  // so the new row joins the whole list rather than standing in for it.
  await client.query({ ...options, staleTime: "static" });
  client.setQueryData(options.queryKey, (old) =>
    old === undefined || old.items.some((d) => d.id === doc.id)
      ? old
      : { ...old, items: [doc, ...old.items] },
  );
  await client.invalidateQueries({
    queryKey: knowledgeGetQuotaQueryKey(knowledgeQuotaArgs(projectId)),
  });
}

/** Uploads one file to the project's knowledge; rejects when the server refuses it. */
export function useKnowledgeUpload(
  projectId: string,
): (file: File) => Promise<DocumentDto> {
  const client = useQueryClient();
  return async (file) => {
    const form = new FormData();
    form.append("file", file);
    form.append("project_id", projectId);
    const accepted = await apiUpload({
      path: "/knowledge/documents",
      form,
      idempotencyKey: crypto.randomUUID(),
      schema: zUploadAccepted,
    });
    const doc = acceptedDocument(accepted, file, projectId);
    await showAccepted(client, projectId, doc);
    return doc;
  };
}

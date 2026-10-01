// The knowledge API as the backend answers it (P1-17, FR-15.x): documents of one project
// (or the workspace knowledge base), text entries, links, uploads, pin and tag edits with
// the version read, trash and restore, and the quota. Writes are recorded; an unknown
// document id falls through to the next handler (ProjectFake's brief).
import { http, HttpResponse, type RequestHandler } from "msw";

import type { DocumentDto, Quota } from "../../api/types.gen";
import { makeDocument, makeProblem } from "../factories";
import { Recorder } from "./settings";

const GIB = 1024 ** 3;

export interface KnowledgeFakeInit {
  projectId?: string | null;
  documents?: DocumentDto[];
  quota?: Partial<Pick<Quota, "used_bytes" | "quota_bytes">>;
}

function problem(status: number, code: string, extra: object = {}) {
  return HttpResponse.json(
    { ...makeProblem({ status, code, title: code }), ...extra },
    { status, headers: { "Content-Type": "application/problem+json" } },
  );
}

export class KnowledgeFake {
  readonly recorder = new Recorder();
  readonly projectId: string | null;
  readonly documents = new Map<string, DocumentDto>();
  readonly trashed = new Set<string>();
  /** Multipart uploads: the project and file name of each. */
  readonly uploads: { project_id: string | null; filename: string }[] = [];
  usedBytes: number;
  quotaBytes: number;

  constructor(init: KnowledgeFakeInit = {}) {
    this.projectId = init.projectId ?? null;
    for (const doc of init.documents ?? []) this.documents.set(doc.id, doc);
    this.usedBytes = init.quota?.used_bytes ?? 0;
    this.quotaBytes = init.quota?.quota_bytes ?? 10 * GIB;
  }

  /** The JSON bodies sent with `METHOD path`, in order. */
  sentBodies(method: string, path: string): unknown[] {
    return this.recorder.sent
      .filter((s) => s.method === method && s.path === path)
      .map((s) => s.body);
  }

  writes(): string[] {
    return this.recorder.writes();
  }

  private live(): DocumentDto[] {
    return [...this.documents.values()].filter((d) => !this.trashed.has(d.id));
  }

  private add(overrides: Partial<DocumentDto>): DocumentDto {
    const doc = makeDocument({
      project_id: this.projectId,
      role: null,
      pinned: false,
      version: 1,
      status: "ready",
      trust: "trusted",
      tainted: false,
      label: null,
      tags: [],
      ...overrides,
    });
    this.documents.set(doc.id, doc);
    return doc;
  }

  get handlers(): RequestHandler[] {
    return [
      http.get("/v1/knowledge/quota", ({ request }) => {
        const projectId = new URL(request.url).searchParams.get("project_id");
        return HttpResponse.json({
          project_id: projectId,
          count: this.live().length,
          used_bytes: this.usedBytes,
          project_bytes: this.usedBytes,
          quota_bytes: this.quotaBytes,
        });
      }),
      http.get("/v1/knowledge/documents", () =>
        HttpResponse.json({ items: this.live(), next_cursor: null }),
      ),
      http.post("/v1/knowledge/documents/text", async ({ request }) => {
        const sent = await this.recorder.record(request);
        const body = sent.body as { title: string; body_md?: string };
        const doc = this.add({
          kind: "text",
          source: "text",
          title: body.title,
          body_md: body.body_md ?? "",
        });
        return HttpResponse.json(doc, { status: 201 });
      }),
      http.post("/v1/knowledge/documents/link", async ({ request }) => {
        const sent = await this.recorder.record(request);
        const body = sent.body as { url: string; title?: string | null };
        const doc = this.add({
          kind: "link",
          source: "link",
          title: body.title ?? body.url,
          provider_url: body.url,
          body_md: null,
        });
        return HttpResponse.json(doc, { status: 201 });
      }),
      http.post("/v1/knowledge/documents", async ({ request }) => {
        const form = await request.clone().formData();
        const file = form.get("file");
        const filename = file instanceof File ? file.name : "";
        this.recorder.sent.push({
          method: "POST",
          path: "/v1/knowledge/documents",
          search: "",
          idempotencyKey: request.headers.get("Idempotency-Key"),
          body: null,
        });
        const projectId = form.get("project_id");
        this.uploads.push({
          project_id: typeof projectId === "string" ? projectId : null,
          filename,
        });
        const doc = this.add({
          kind: "file",
          source: "upload",
          title: filename,
          status: "pending_scan",
          trust: "untrusted",
          tainted: true,
          body_md: null,
        });
        return HttpResponse.json(
          {
            id: doc.id,
            status: "pending_scan",
            version_id: crypto.randomUUID(),
          },
          { status: 202 },
        );
      }),
      http.get("/v1/knowledge/documents/:id", ({ params }) => {
        const doc = this.documents.get(String(params.id));
        if (!doc) return undefined;
        return HttpResponse.json(doc);
      }),
      http.patch("/v1/knowledge/documents/:id", async ({ params, request }) => {
        const doc = this.documents.get(String(params.id));
        if (!doc) return undefined;
        const sent = await this.recorder.record(request);
        const body = sent.body as Partial<DocumentDto> & { version: number };
        if (body.version !== doc.version) {
          return problem(409, "stale_version", { current: doc });
        }
        const next = { ...doc, ...body, version: doc.version + 1 };
        this.documents.set(doc.id, next);
        return HttpResponse.json(next);
      }),
      http.delete(
        "/v1/knowledge/documents/:id",
        async ({ params, request }) => {
          const id = String(params.id);
          if (!this.documents.has(id)) return undefined;
          await this.recorder.record(request);
          this.trashed.add(id);
          return new HttpResponse(null, { status: 204 });
        },
      ),
      http.post(
        "/v1/knowledge/documents/:id/restore",
        async ({ params, request }) => {
          const id = String(params.id);
          const doc = this.documents.get(id);
          if (!doc) return problem(404, "not_found");
          await this.recorder.record(request);
          this.trashed.delete(id);
          return HttpResponse.json(doc);
        },
      ),
    ];
  }
}

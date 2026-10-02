// MSW handlers for Settings > Connections (P3-02): the providers that can be connected
// (one behind a sign-in with a consent notice, one without sign-in) and the connections
// a test starts from. The handlers keep their own copy of the rows, so a create, an edit
// or a disconnect shows on the next read. Tests add them with `server.use(...)` and read
// what was sent from a `Recorder`.
import { http, HttpResponse, type RequestHandler } from "msw";

import type {
  ConnectionCreate,
  ConnectionOut,
  ConnectionPatch,
  ProviderOut,
} from "../../api/types.gen";

import type { Recorder } from "./settings";

export const AUTHORIZE_URL =
  "https://sign-in.example.com/authorize?client_id=demo-client&state=demo-state";

export const PROVIDERS: readonly ProviderOut[] = [
  {
    provider: "fake",
    label: "Example mail",
    kind: "email",
    auth: "oauth",
    backfill_cap_days: 90,
    consent_notice:
      "Example mail shares your messages with Tumnis. Only you can see them.",
    sync_every_min: 5,
  },
  {
    provider: "fake_open",
    label: "Example notes",
    kind: "notes",
    auth: "none",
    backfill_cap_days: null,
    consent_notice: null,
    sync_every_min: 15,
  },
];

export function connectionRow(
  overrides: Partial<ConnectionOut> = {},
): ConnectionOut {
  return {
    id: "01890000-0000-7000-8000-0000000003b1",
    kind: "email",
    provider: "fake",
    account_label: "Work mail",
    status: "ok",
    status_detail: null,
    last_success_at: "2026-03-09T11:55:00Z",
    next_sync_at: "2026-03-09T12:00:00Z",
    settings: {
      backfill_days: 30,
      sync_every_min: null,
      allowlist: [],
      extra: {},
    },
    version: 2,
    ...overrides,
  };
}

const NEW_ID = "01890000-0000-7000-8000-0000000003bf";

export interface ConnectionsFakeOptions {
  /** How many `oauth/url` reads answer null before the sign-in page is ready. */
  pendingPolls?: number;
}

/** The connection routes over an in-memory list. */
export class ConnectionsFake {
  rows: ConnectionOut[];
  private polls = 0;

  constructor(
    rows: readonly ConnectionOut[] = [connectionRow()],
    private readonly options: ConnectionsFakeOptions = {},
  ) {
    this.rows = rows.map((row) => ({ ...row }));
  }

  handlers(recorder: Recorder): RequestHandler[] {
    const find = (id: unknown) => this.rows.find((row) => row.id === id);
    const notFound = () =>
      HttpResponse.json(
        {
          type: "about:blank",
          title: "Not found",
          status: 404,
          code: "not_found",
        },
        { status: 404 },
      );
    return [
      http.get("*/v1/connections/providers", () =>
        HttpResponse.json(PROVIDERS),
      ),
      http.get("*/v1/connections", () => HttpResponse.json(this.rows)),
      http.post("*/v1/connections", async ({ request }) => {
        const sent = await recorder.record(request);
        const body = sent.body as ConnectionCreate;
        const provider = PROVIDERS.find((p) => p.provider === body.provider);
        if (!provider) return notFound();
        const row = connectionRow({
          id: NEW_ID,
          kind: provider.kind,
          provider: provider.provider,
          account_label: body.account_label,
          status: provider.auth === "none" ? "ok" : "pending_auth",
          last_success_at: null,
          next_sync_at: null,
          settings: {
            backfill_days: 30,
            sync_every_min: null,
            allowlist: [],
            extra: {},
            ...body.settings,
          },
          version: 1,
        });
        this.rows.push(row);
        return HttpResponse.json(row, { status: 201 });
      }),
      http.get("*/v1/connections/:connectionId", ({ params }) => {
        const row = find(params.connectionId);
        return row ? HttpResponse.json(row) : notFound();
      }),
      http.patch(
        "*/v1/connections/:connectionId",
        async ({ request, params }) => {
          const sent = await recorder.record(request);
          const row = find(params.connectionId);
          if (!row) return notFound();
          const body = sent.body as ConnectionPatch;
          if (body.version !== row.version) {
            return HttpResponse.json(
              {
                type: "about:blank",
                title: "Conflict",
                status: 409,
                code: "version_conflict",
                current: row,
              },
              { status: 409 },
            );
          }
          const next: ConnectionOut = {
            ...row,
            account_label: body.account_label ?? row.account_label,
            settings: { ...row.settings, ...body.settings },
            version: row.version + 1,
          };
          this.rows = this.rows.map((r) => (r.id === row.id ? next : r));
          return HttpResponse.json(next);
        },
      ),
      http.delete(
        "*/v1/connections/:connectionId",
        async ({ request, params }) => {
          await recorder.record(request);
          if (!find(params.connectionId)) return notFound();
          this.rows = this.rows.filter((r) => r.id !== params.connectionId);
          return new HttpResponse(null, { status: 204 });
        },
      ),
      http.post(
        "*/v1/connections/:connectionId/oauth/start",
        async ({ request, params }) => {
          await recorder.record(request);
          if (!find(params.connectionId)) return notFound();
          this.polls = 0;
          return HttpResponse.json(
            { workflow_id: `connect_oauth:${String(params.connectionId)}` },
            { status: 202 },
          );
        },
      ),
      http.get(
        "*/v1/connections/:connectionId/oauth/url",
        async ({ request, params }) => {
          await recorder.record(request);
          if (!find(params.connectionId)) return notFound();
          this.polls += 1;
          const ready = this.polls > (this.options.pendingPolls ?? 1);
          return HttpResponse.json({
            authorize_url: ready ? AUTHORIZE_URL : null,
          });
        },
      ),
      http.post(
        "*/v1/connections/:connectionId/sync",
        async ({ request, params }) => {
          await recorder.record(request);
          const row = find(params.connectionId);
          if (!row) return notFound();
          return HttpResponse.json(row, { status: 202 });
        },
      ),
    ];
  }
}

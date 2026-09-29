// MSW handlers for the Settings endpoints (P0-26), with realistic pages: two audit pages,
// three dead letters, three sessions. The shapes follow the generated types, so a change
// to the API fails type-checking here first. Tests add them with `server.use(...)` and
// read what was sent from a `Recorder`.
import { http, HttpResponse, type RequestHandler } from "msw";

import type {
  AccountOut,
  AuditEntry,
  DeadLetterOut,
  KeyOut,
  PageAuditEntry,
  SessionOut,
  WorkspaceSettingsOut,
} from "../../api/types.gen";

export interface Sent {
  method: string;
  path: string;
  search: string;
  idempotencyKey: string | null;
  body: unknown;
}

/** Every request the recorded handlers saw, in order. */
export class Recorder {
  readonly sent: Sent[] = [];

  async record(request: Request): Promise<Sent> {
    const text = await request.clone().text();
    const url = new URL(request.url);
    const sent: Sent = {
      method: request.method,
      path: url.pathname,
      search: url.search,
      idempotencyKey: request.headers.get("Idempotency-Key"),
      body: text ? (JSON.parse(text) as unknown) : null,
    };
    this.sent.push(sent);
    return sent;
  }

  /** `METHOD /path` of each write (reads left out). */
  writes(): string[] {
    return this.sent
      .filter((s) => s.method !== "GET")
      .map((s) => `${s.method} ${s.path}`);
  }
}

// --- Workspace ---------------------------------------------------------------------

export function workspaceSettings(
  overrides: Partial<WorkspaceSettingsOut> = {},
): WorkspaceSettingsOut {
  return {
    timezone: "America/New_York",
    subtask_threshold_min: 30,
    version: 4,
    ...overrides,
  };
}

export function workspaceHandlers(
  recorder: Recorder,
  loaded: WorkspaceSettingsOut = workspaceSettings(),
  answer: (body: Record<string, unknown>) => Response = (body) =>
    HttpResponse.json({
      ...loaded,
      ...body,
      version: loaded.version + 1,
    }),
): RequestHandler[] {
  return [
    http.get("*/v1/settings/workspace", () => HttpResponse.json(loaded)),
    http.put("*/v1/settings/workspace", async ({ request }) => {
      const sent = await recorder.record(request);
      return answer(sent.body as Record<string, unknown>);
    }),
  ];
}

// --- Keys --------------------------------------------------------------------------

export const KEY_ID = "01890000-0000-7000-8000-0000000000aa";

export function keyRow(overrides: Partial<KeyOut> = {}): KeyOut {
  return {
    id: KEY_ID,
    name: "laptop script",
    prefix: "abcdefghijkl",
    scopes: ["tasks:read"],
    project_ids: null,
    created_at: "2026-03-09T12:00:00Z",
    expires_at: null,
    last_used_at: null,
    revoked_at: null,
    ...overrides,
  };
}

// --- Audit -------------------------------------------------------------------------

export function auditEntry(seq: number, action: string): AuditEntry {
  return {
    id: `01890000-0000-7000-8000-${seq.toString().padStart(12, "0")}`,
    seq,
    occurred_at: `2026-03-09T${String(10 + (seq % 10)).padStart(2, "0")}:00:00Z`,
    actor_type: "user",
    actor_id: "01890000-0000-7000-8000-00000000beef",
    action,
    target_type: null,
    target_id: null,
    source_ip: "192.0.2.10",
    user_agent: null,
    correlation_id: `req-${String(seq)}`,
    reason: null,
    details: {},
  };
}

export const AUDIT_NEXT = "cursor-page-2";
export const AUDIT_PAGES: readonly PageAuditEntry[] = [
  {
    items: [auditEntry(4, "key.created"), auditEntry(3, "auth.login")],
    next_cursor: AUDIT_NEXT,
  },
  {
    items: [
      auditEntry(2, "settings.changed"),
      auditEntry(1, "setup.completed"),
    ],
    next_cursor: null,
  },
];

export function auditHandlers(recorder: Recorder): RequestHandler[] {
  return [
    http.get("*/v1/audit", async ({ request }) => {
      await recorder.record(request);
      const cursor = new URL(request.url).searchParams.get("cursor");
      return HttpResponse.json(AUDIT_PAGES[cursor === AUDIT_NEXT ? 1 : 0]);
    }),
  ];
}

// --- Dead letters ------------------------------------------------------------------

export function deadLetter(n: number): DeadLetterOut {
  return {
    id: `01890000-0000-7000-8000-0000000000d${String(n)}`,
    event_id: `01890000-0000-7000-8000-0000000000e${String(n)}`,
    event_name: "task.created",
    subscriber: `usage.count_task_created_${String(n)}`,
    error: `RuntimeError: boom ${String(n)}`,
    attempts: 5,
    retries: 0,
    last_at: "2026-03-09T11:00:00Z",
    status: "open",
    version: n,
  };
}

export const DEAD_LETTERS: readonly DeadLetterOut[] = [
  deadLetter(1),
  deadLetter(2),
  deadLetter(3),
];

export function deadLetterHandlers(recorder: Recorder): RequestHandler[] {
  return [
    http.get("*/v1/dead-letters", () =>
      HttpResponse.json({ items: DEAD_LETTERS, next_cursor: null }),
    ),
    http.post("*/v1/dead-letters/:id/retry", async ({ request, params }) => {
      await recorder.record(request);
      const row = DEAD_LETTERS.find((d) => d.id === params.id);
      return HttpResponse.json({ ...row, status: "retrying", retries: 1 });
    }),
    http.post("*/v1/dead-letters/:id/discard", async ({ request, params }) => {
      await recorder.record(request);
      const row = DEAD_LETTERS.find((d) => d.id === params.id);
      return HttpResponse.json({ ...row, status: "discarded" });
    }),
  ];
}

// --- Sessions ----------------------------------------------------------------------

export function sessionRow(
  n: number,
  overrides: Partial<SessionOut> = {},
): SessionOut {
  return {
    id: `01890000-0000-7000-8000-0000000000c${String(n)}`,
    device_label: null,
    user_agent: `Browser ${String(n)}`,
    source_ip: `192.0.2.${String(n)}`,
    created_at: "2026-03-01T09:00:00Z",
    last_seen_at: "2026-03-09T11:00:00Z",
    expires_at: "2026-04-08T11:00:00Z",
    current: n === 1,
    ...overrides,
  };
}

/** Three sessions, the first one current; signing out the others leaves only it. */
export function sessionHandlers(recorder: Recorder): RequestHandler[] {
  let rows = [sessionRow(1), sessionRow(2), sessionRow(3)];
  return [
    http.get("*/v1/auth/sessions", () =>
      HttpResponse.json({ items: rows, next_cursor: null }),
    ),
    http.delete("*/v1/auth/sessions", async ({ request }) => {
      await recorder.record(request);
      rows = rows.filter((row) => row.current);
      return new HttpResponse(null, { status: 204 });
    }),
    http.delete("*/v1/auth/sessions/:id", async ({ request, params }) => {
      await recorder.record(request);
      rows = rows.filter((row) => row.id !== params.id);
      return new HttpResponse(null, { status: 204 });
    }),
  ];
}

// --- Account -----------------------------------------------------------------------

export const NEW_TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP";

export function account(overrides: Partial<AccountOut> = {}): AccountOut {
  return {
    user_id: "01890000-0000-7000-8000-00000000beef",
    email: "scott@example.com",
    second_factor: "totp",
    totp_confirmed_at: "2026-03-01T09:00:00Z",
    ...overrides,
  };
}

export function accountHandlers(recorder: Recorder): RequestHandler[] {
  let current = account();
  return [
    http.get("*/v1/auth/account", () => HttpResponse.json(current)),
    http.post("*/v1/auth/totp/enrol", async ({ request }) => {
      await recorder.record(request);
      return HttpResponse.json({
        otpauth_uri: `otpauth://totp/Tumnis%20Guide:scott%40example.com?secret=${NEW_TOTP_SECRET}&issuer=Tumnis%20Guide`,
        enrol_token: "enrol-token-1",
      });
    }),
    http.post("*/v1/auth/totp/enrol/confirm", async ({ request }) => {
      await recorder.record(request);
      current = account({ totp_confirmed_at: "2026-03-09T12:00:00Z" });
      return new HttpResponse(null, { status: 204 });
    }),
  ];
}

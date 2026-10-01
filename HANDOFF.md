# P4-05 handoff (Browser push)

Branch: `wp/P4-05` (pushed). **No PR is open yet.** Push only with
`/usr/bin/git push origin HEAD:wp/P4-05`. Binding instructions:
`~/tumnis-coordinator/prompts/wave1/P4-05.txt` (read it and the rule files it names).
Scratch: `$TMPDIR/P4-05-c0/` (`check.sh` runs `make check` with the semgrep env vars set;
`unmark.py <file> <test>` removes one test's `spec:P4-05` xfail marker).

## Commits (on top of main fb7d771)

| SHA | What |
| --- | --- |
| 618c701 | test(notifications): P4-05 spec tests (red): T-01..07 + stubs; pins `http-ece==1.2.1`, `py-vapid==1.9.4` |
| 829e7e4 | test(frontend): P4-05 spec tests (red): T-08, T-09 (Vitest test.fails), T-10 (Playwright test.fail) + stubs |
| ac99db9 | feat(notifications): push rules + P2-16's delivery_decision/flush_due (T-01..03 green, extra unit tests) |
| e92b43d | feat(notifications): Web Push client and fake (T-07 green, fake and real) |

## Spec test state

| ID | File | State |
| --- | --- | --- |
| T-P4-05-01..03 | `backend/tumnis/modules/notifications/tests/unit/test_push_rules.py` | green, unmarked |
| T-P4-05-07 | `.../tests/contract/test_webpush_adapter.py` (TestWebPushFake / TestWebPushReal) | green, unmarked |
| T-P4-05-04..06 | `.../tests/integration/test_push_delivery.py` (+ `_push.py`, `conftest.py`) | RED (xfail) |
| T-P4-05-08 | `frontend/src/sw.test.ts` | RED (test.fails) |
| T-P4-05-09 | `frontend/src/lib/push.test.ts` (two tests) | RED (test.fails) |
| T-P4-05-10 | `frontend/e2e/acceptance/P4-05-push-deeplink.spec.ts` | RED (test.fail) |

## Key decisions (already reflected in code/tests)

1. **P2-16 seam.** P2-16 (Discord delivery) is not built; its needs (P2-12 #134) are open.
   P4-05 builds the smallest seam under P2-16's planned names: `rules.delivery_decision`
   (batch iff level == quiet and a task In progress; else now) and `rules.flush_due`
   (non-empty batch and (no task In progress or now >= day_end_at)). Main was messaged
   about this. T-05's "push exactly when Discord is" = push follows that table.
2. **No pywebpush.** Its sender posts through `requests`/`aiohttp`, which AGENTS.md forbids
   for outbound calls. Instead: `http_ece.encrypt(..., version="aes128gcm")` (what
   pywebpush's `WebPusher.encode` calls) and `py_vapid.Vapid02.from_raw(b64url).sign(claims)`
   (RFC 8292), posted through `tumnis.core.net.guarded_client`. Untyped imports use
   `# type: ignore[import-untyped]` (house style; no mypy config edit).
3. **Push table** = every registered review kind (`review_item.added`) + every
   `focus.event`. Question/approval/result already fan into `review_item.added`; do not
   double-subscribe.
4. **Payloads** (`rules.push_payload`): review item -> title "Waiting on you: <kind words>
   in <project>" (email-like words dropped, 60 chars max), body "Open Tumnis to review
   it.", url `/review?kind=<kind>&item=<id>`, tag item id. Focus event -> kind
   `focus.<kind>`, fixed title per kind, url `/` (the dashboard and its focus bar), tag
   event id. Batch flush -> `batch_payload(n)`: url `/review`, tag `batch`, kind `batch`.
5. **SW safe URLs**: `/review` with only the zod-valid `kind`/`item` params kept, or a bare
   `/`; everything else (cross-origin, other paths, non-strings) -> `/review`.
6. **Push settings UI** lives in Settings > Account (`components/settings/PushSettings.tsx`),
   no new Settings section. Permission prompt only on the "Enable push" press; when
   `Notification.permission === "denied"`: show text matching
   /Notifications are blocked for this site/ and no "Enable push" button. After success:
   text /Push is on for this device/.

## Remaining TDD steps (plan order)

### Step 3: T-04, 05, 06 (integration) - push channel in notifications
- Migration `backend/tumnis/modules/notifications/migrations/0001_notifications.py`:
  `revision = "notifications_0001"`, `down_revision = None`,
  `branch_labels = ("notifications",)`, `depends_on = ("auth_0001",)`, `phase = "expand"`,
  tables via `create_tenant_table` (copy focus_0001's style):
  - `push_subscriptions(user_id uuid, endpoint text, p256dh text, auth text,
    user_agent text null, last_success_at tz null, failures int default 0)`, unique
    `(workspace_id, endpoint)` (partial `deleted_at IS NULL` if soft delete is used;
    T-06 reads `deleted_at IS NULL` rows, so either hard or soft delete works).
  - `notifications(kind text, target_type text, target_id uuid, project_id uuid null,
    level text, decision text ('now'|'batch'), released_at tz null, payload jsonb,
    dedupe_key text)` unique `(workspace_id, dedupe_key)` (dedupe = event id).
  - `delivery_attempts(notification_id uuid, channel text ('push'), subscription_id uuid
    null (no FK), status text ('sent'|'gone'|'failed'|'rejected'), status_code int null,
    attempted_at tz)`. T-06 asserts columns `channel`, `status`, `status_code`,
    `subscription_id`; and `push_subscriptions.failures`, `last_success_at`, `endpoint`.
  - Check `alembic heads` first; check the table-registry meta-test
    (`backend/tests/meta/test_table_registry.py`) and `docs` Part A row for notifications.
- `models.py` mirrors (TenantBase, Base), like `focus/models.py`.
- `api.py`: VAPID key get-or-create in `settings_store` (key e.g. `notifications.vapid`,
  model with `private_key` b64url raw 32 bytes, `public_key` b64url uncompressed 65 bytes,
  `subject` "mailto:<email of the user who enabled push>"; handle `StaleVersion` on the
  create race by re-reading); `subscribe` (upsert by endpoint, refuse
  `not rules.endpoint_allowed` with 422 `endpoint_not_allowed`), `unsubscribe`;
  notification record + decide (level from `focus.api.current(ctx, now).level`, in
  progress from `tasks.api.list_tasks(s, status=Status.IN_PROGRESS, limit=1).total > 0`,
  project name from `projects.api.project_names`); attempt recording.
- `router.py`: `root_router = v1_router("notifications", tags=[...])` (planning's
  `root_router` pattern, mounted by `app.py` via `module_routers("root_router")`):
  `GET /push/vapid-public-key` -> `{"public_key": ...}`, `POST /push/subscriptions` (201,
  `idempotent=True`, body `{endpoint, keys:{p256dh, auth}}`), `DELETE
  /push/subscriptions/{id}`; `auth="session"`; check the route-registry, authz-matrix and
  CSRF meta-tests. The api never calls out (subscriptions only store).
- `events.py` subscribers (idempotent): `review_item.added` -> notifications.push_review_item;
  `focus.event` -> notifications.push_focus_event (a `day_end` event also flushes);
  `task.status_changed` with `from == in_progress` -> flush check; `focus.level_changed`
  -> flush when the new level no longer batches.
- `workflows.py`: `use(factory, *, retry_delays_s)` seam (the conftest calls
  `use(lambda workspace_id, vapid: fake, retry_delays_s=(0.05, 0.05))` and `use()` to reset);
  a DBOS workflow named `notifications.deliver_push` (the `_push.py` settle waits on that
  name and `deliver_event`) on a new queue registered in `tumnis/worker.py
  register_queues()`; per subscription: send step (catches adapter errors, records an
  attempt row, gone -> delete subscription, failed -> failures+1, sent -> failures=0 and
  last_success_at), retry with `DBOS.sleep_async(delay)` between attempts, 3 attempts by
  default. Real mode resolves `notifications.webpush` with `vapid`, `policy`
  (`Settings().net_policy()`), clock. Default TTL e.g. 24 h.
- Then unmark T-04, T-05, T-06 one at a time; they need Docker: rely on CI or one bare
  `make test-int` (DOCKER LOAD RULE: at most once per continuation).

### Step 4: T-09 - subscribe flow and endpoints
- `make gen` after the routes exist (regenerates `schemas/openapi.json`, the TS client;
  never hand-edit `frontend/src/api/`).
- `frontend/src/lib/push.ts`: status from `Notification.permission`, `"PushManager" in
  window`, standalone (`matchMedia("(display-mode: standalone)")` or `navigator.standalone`)
  and iOS UA -> "install"; `enablePush()`: `Notification.requestPermission()` only here,
  `GET /v1/push/vapid-public-key`, `registration.pushManager.subscribe({userVisibleOnly:
  true, applicationServerKey: <Uint8Array from base64url>})` (test key "AQIDBA" ->
  [1,2,3,4]), then POST `{endpoint, keys}` through `apiFetch` (adds Idempotency-Key + CSRF).
- `PushSettings.tsx` mounted in `AccountSection.tsx`; must make NO request on mount
  (MSW "error" strategy would fail AccountSection's tests). Settings route is lazy, so the
  200 KB initial-JS budget is unaffected.

### Step 5: T-08 - service worker (`injectManifest`)
- `vite.config.ts`: `strategies: "injectManifest", srcDir: "src", filename: "sw.ts"`, keep
  the manifest, move `globPatterns` to `injectManifest: { globPatterns }`, keep
  `injectRegister: false`, `registerType: "autoUpdate"`.
- `src/sw.ts`: export `safeReviewUrl(raw, origin)`, `openOrFocus(url, clients, origin)`
  (matchAll window clients -> navigate(absolute url) + focus, else openWindow(absolute
  url)), a zod `PushPayloadSchema`; install the handlers only when running as a service
  worker (guard on `typeof ServiceWorkerGlobalScope !== "undefined"`), so Vitest can import
  it: `precacheAndRoute(self.__WB_MANIFEST)`, `cleanupOutdatedCaches()`,
  `registerRoute(new NavigationRoute(createHandlerBoundToURL("/index.html"),
  { denylist: NOT_THE_SHELL }))`, `self.skipWaiting()`, `clientsClaim()`, `push` and
  `notificationclick` handlers as in the plan. Import `./lib/zodConfig` first (CSP).
  Move `reviewSearch` from `routes/review.tsx` to a small `lib/` module and import it in
  both (the SW must not import the route file).
- Pin devDeps `workbox-core`, `workbox-precaching`, `workbox-routing` at 7.4.1 (installed
  transitively already) in `frontend/package.json` (shared file: list it).
- tsconfig has only the DOM lib: declare the few SW types locally instead of adding the
  WebWorker lib. Run the WHOLE Vitest suite afterwards (VitePWA runs under Vitest too), the
  build, `npm run bundle` (200 KB budget), and check the CSP/offline e2e (A0.2, csp.spec).

### Step 6: T-10 - e2e (CI authority; uses A2.2's scripted question for a real item).

### Then
`make check`, unit/contract (`-n 3`), integration (CI), Vitest, Playwright; open the PR
(body: summary, per-layer results, shared-file edits, deviations, Scott items, docs cited,
ending with the Claude Code line), `gh pr comment <url> --body "@coderabbitai review"`,
run the review loop (`~/tumnis-coordinator/pr-review-loop.md`), then SendMessage main
"#<PR> MERGE-READY at <sha>".

## Shared-file edits so far
- `backend/pyproject.toml` + `backend/uv.lock`: added `http-ece==1.2.1`, `py-vapid==1.9.4`.

## Deviations to list in the PR
- P2-16 seam built here (decision 1). T-05 compares against P2-16's table, not Discord.
- No pywebpush (decision 2); same encryption and VAPID via its two building blocks.
- Contract test ids are `TestWebPushFake::test_contract` / `TestWebPushReal::test_contract`
  (the AdapterContract class pattern) rather than `test_contract[fake]`.
- Focus pushes open `/` (the plan names only `/review` deep links).
- Plan says size S; with the seam and the SW switch it is closer to M.

## Docs relied on (cite in the PR)
- pywebpush `WebPusher.encode` / `webpush()` source (github.com/web-push-libs/pywebpush),
  py_vapid `Vapid02.sign`/`from_raw` (github.com/web-push-libs/vapid), http_ece `encrypt`.
- RFC 8030 (TTL, 404/410), RFC 8188/8291 (aes128gcm), RFC 8292 (VAPID).
- vite-plugin-pwa injectManifest guide (vite-pwa-org.netlify.app/guide/inject-manifest.html).
- Still to read: MDN Push API / Notifications / Clients.openWindow, web.dev push, WebKit
  "Web Push for web apps on iOS" (iOS 16.4+, Home Screen only).

## Scott items
- None blocking yet. Possibly: confirm the P2-16 seam approach and that pywebpush's
  sender is replaced (both are noted for main).

## Verify
```
bash $TMPDIR/P4-05-c0/check.sh
cd backend && uv run pytest -q -p no:randomly -m "not integration" tumnis/modules/notifications
cd frontend && npx vitest run src/sw.test.ts src/lib/push.test.ts --maxWorkers=2
make test-int   # bare, from the worktree root, at most once
```

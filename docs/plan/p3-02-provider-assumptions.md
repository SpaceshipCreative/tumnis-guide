# P3-02 provider assumptions (for P3-01 to check)

P3-02 built the connector framework against fakes, before P3-01's live provider learning tests (Scott decision 71; decision 80 defers P3-01 and the provider connectors P3-03 to P3-07 and P3-11). Wherever the framework depends on how Inbox Zero or Granola behave, it assumes the behaviour below. Each item names where the assumption lives in code, so P3-01 can confirm it against a recording or change it in one place.

Status: **unverified** for every item until P3-01 runs with Scott's accounts.

## Signing in (OAuth, `adapters/oauth.py`, `api.prepare_oauth`, `api.complete_oauth`)

| # | Assumption | Source of the assumption | Where it lives | If wrong |
| --- | --- | --- | --- | --- |
| 1 | Both MCP servers follow the MCP authorization spec: protected-resource metadata (RFC 9728) at the path-based or root well-known URL names one authorization server, whose RFC 8414 metadata names the authorize, token and registration endpoints. | MCP authorization spec; Granola MCP help page (browser sign-in with dynamic client registration) | `McpOAuthClient.discover` (the MCP SDK 1.30.0 discovery order) | Add the provider's endpoints to its `ProviderSpec` and skip discovery. |
| 2 | Dynamic client registration (RFC 7591) accepts a public client: `token_endpoint_auth_method: "none"`, grants `authorization_code` and `refresh_token`, our callback as the only redirect URI. | MCP authorization spec | `api.client_metadata` | Store a client secret sealed with the grant and send it on token calls. |
| 3 | The authorize endpoint takes PKCE S256 and the RFC 8707 `resource` parameter (the MCP server URL); the token endpoint takes `resource` too. | MCP authorization spec | `api.prepare_oauth`, `McpOAuthClient._token` | Drop `resource` for that provider. |
| 4 | The token endpoint issues a refresh token, and may rotate it on refresh (we store the new one before using the new access token; one we do not get back stays valid, RFC 6749 section 6). Granola does not document refresh-token issuance: the open question P3-01's probe answers. | Plan P3-01; RFC 6749 | `api.access_token` | Without refresh tokens a connection goes `auth_required` when its access token expires (about hourly), and the user signs in again: the status and review item already handle it. |
| 5 | A revoked or spent grant answers HTTP 400 with `{"error": "invalid_grant"}` (RFC 6749 section 5.2). Only the `error` code is kept, never the description. | RFC 6749 | `McpOAuthClient._token`, `OAuthRefused` | Map the provider's answer to `OAuthRefused` in the client. |
| 6 | The callback may or may not carry `iss` (RFC 9207); it is stored when present and not required. | RFC 9207 | `api.accept_connection_callback` | Require it for a provider that sends it. |
| 7 | Neither provider reports the account's identity at sign-in, so a connection's `account` is its own id (a placeholder) and the user's `account_label` names it. Rate limits are keyed per connection, so two connections of one real account would each get the whole limit. | No identity endpoint in the MCP tool lists the plan names | `api.create_connection`, `api.provider_limit_key` | Read the identity from the token response or a profile tool, and key the limit on it. |

## Reading (MCP tools, `adapters/mcp_client.py`)

| # | Assumption | Source of the assumption | Where it lives | If wrong |
| --- | --- | --- | --- | --- |
| 8 | Both servers speak Streamable HTTP and accept the access token as `Authorization: Bearer`. An expired or revoked token answers HTTP 401. | MCP transports and authorization specs | `McpSource.call` (401 raises `ReauthRequired`) | Map the provider's signal in the connector. |
| 9 | Inbox Zero's tools are `search_inbox`, `read_thread` and `create_draft` (the one write, a draft); none sends. | Plan P3-03 (the client exposes no send) | `TOOL_ALLOWLISTS["inbox_zero"]` | Rename in the allow-list; T-P3-02-13 keeps it free of send tools. |
| 10 | Granola's Basic plan offers `list_meetings` and `get_meetings`, and no folder, search or transcript tools. | Plan P3-01; Granola MCP help page | `TOOL_ALLOWLISTS["granola"]` | Rename in the allow-list. |
| 11 | A server answers a 5xx or drops the connection when it is down: `AdapterUnavailable`, the connection goes `degraded` and backs off. | Plan | `McpSource.call` | Map the provider's error result in the connector. |

## Limits, cadence and history (`rules.py`)

| # | Assumption | Source of the assumption | Where it lives | If wrong |
| --- | --- | --- | --- | --- |
| 12 | Granola allows about 100 requests a minute across tools; Tumnis uses 90 a minute per account to leave headroom. | Granola MCP help page; plan | `PROVIDER_LIMITS["granola"]` | Change the number. |
| 13 | Inbox Zero's limit is undocumented; Tumnis uses 60 a minute (plan default). | Plan default | `PROVIDER_LIMITS["inbox_zero"]` | Change the number. |
| 14 | Inbox Zero syncs every 5 minutes (PRD) and Granola every 30 (plan default). | PRD, plan | `DEFAULT_SYNC_MIN` | Change the number. |
| 15 | Granola Basic keeps 30 days of meetings, so its backfill cap is 30 days whatever the connection asks for. | Granola MCP help page | the `backfill_cap_days` of Granola's `ProviderSpec` (P3-04 registers it) | Change the cap. |
| 16 | Both providers can list by time from a `since` instant, so the framework's first cursor `{"schema_version", "scope", "since"}` is enough to start a backfill; each connector keeps its own page token inside the cursor. | Plan P3-03 and P3-04 cursor sections | `api.begin_sync`, the connectors | The connector translates `since` into the provider's own paging. |

## Not assumed

- No provider is registered with the framework yet apart from `fake` (fakes only). Inbox Zero and Granola register their `ProviderSpec` (server URL, backfill cap, consent notice) in P3-03 and P3-04, from P3-01's findings.
- `ingest_items` / `POST /ingest` (the P3-01 fallback for a provider that cannot sync) is not built: it stays pending with P3-01 (decision 80).

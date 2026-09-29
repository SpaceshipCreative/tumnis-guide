// Settings > Calendar (P1-09, FR-1.3): the Google accounts Tumnis reads events from. Each
// account shows its sync status (last sync, or `Needs reauth` with Reconnect after Google
// revoked the grant), the calendars to sync (a toggle sends PUT with the new choice and
// the version read) and Sync now. Connect asks the server for Google's consent URL and
// opens it; Google sends the browser back to the callback, which returns here. The OAuth
// client (Scott's own Google Cloud client) is set below; its secret is write-only.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { calendarListAccountsQueryKey } from "../../api/@tanstack/react-query.gen";
import { calendarOauthStart } from "../../api/sdk.gen";
import type {
  CalendarAccountOut,
  SettingSectionOut,
} from "../../api/types.gen";
import { ApiError, apiWrite, ConflictError, useWrite } from "../../lib/fetch";
import { calendarAccountsQuery, calendarOAuthClientQuery } from "./queries";
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

export interface CalendarSectionProps {
  /** Opens Google's consent page (the whole window by default). */
  openUrl?: (url: string) => void;
}

function problemText(error: unknown, fallback: string): string {
  if (error instanceof ConflictError)
    return "That changed elsewhere; the list is up to date now.";
  if (error instanceof ApiError) return error.problem.detail ?? error.message;
  return fallback;
}

export function CalendarSection({
  openUrl = (url) => {
    window.location.assign(url);
  },
}: CalendarSectionProps) {
  const accounts = useQuery(calendarAccountsQuery());
  const [message, setMessage] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);

  async function connect() {
    setMessage(null);
    setConnecting(true);
    try {
      const { data } = await calendarOauthStart({ throwOnError: true });
      openUrl(data.url);
    } catch (error) {
      setMessage(problemText(error, "Google could not be reached."));
    } finally {
      setConnecting(false);
    }
  }

  const items = accounts.data ?? [];
  return (
    <section aria-labelledby="calendar-title" className={SECTION}>
      <h2 id="calendar-title" className={HEADING}>
        Calendar
      </h2>
      <p className={HINT}>
        Tumnis reads events and busy times from your Google accounts, read-only.
        The planner uses the calendars you choose.
      </p>
      <p role="status" className={HINT}>
        {message}
      </p>
      {accounts.isError && (
        <p role="alert" className={ERROR}>
          The calendar accounts could not be loaded.
        </p>
      )}
      {accounts.isSuccess && items.length === 0 && (
        <p className={HINT}>No Google account is connected yet.</p>
      )}
      <ul className="flex flex-col gap-2">
        {items.map((account) => (
          <li key={account.id}>
            <AccountCard
              account={account}
              busy={connecting}
              onReconnect={() => void connect()}
              onMessage={setMessage}
            />
          </li>
        ))}
      </ul>
      <div>
        <button
          type="button"
          className={BUTTON}
          disabled={connecting}
          onClick={() => void connect()}
        >
          Connect Google account
        </button>
      </div>
      <OAuthClientForm />
    </section>
  );
}

function AccountCard({
  account,
  busy,
  onReconnect,
  onMessage,
}: {
  account: CalendarAccountOut;
  busy: boolean;
  onReconnect: () => void;
  onMessage: (message: string | null) => void;
}) {
  const queryClient = useQueryClient();
  const replace = (next: CalendarAccountOut) => {
    queryClient.setQueryData<CalendarAccountOut[]>(
      calendarListAccountsQueryKey(),
      (current) => current?.map((a) => (a.id === next.id ? next : a)),
    );
  };
  const refresh = () =>
    queryClient.invalidateQueries({
      queryKey: calendarListAccountsQueryKey(),
    });

  const choose = useWrite<
    { selected: string[]; idempotencyKey?: string },
    CalendarAccountOut
  >({
    mutationFn: ({ selected, idempotencyKey }) =>
      apiWrite<CalendarAccountOut>({
        kind: "update",
        method: "PUT",
        path: `/calendar/accounts/${encodeURIComponent(account.id)}/calendars`,
        body: { selected_calendar_ids: selected },
        version: account.version,
        idempotencyKey,
      }),
    onSuccess: replace,
    onError: (error) => {
      onMessage(problemText(error, "The calendars could not be saved."));
      void refresh();
    },
  });

  const syncNow = useWrite<{ idempotencyKey?: string }, CalendarAccountOut>({
    mutationFn: ({ idempotencyKey }) =>
      apiWrite<CalendarAccountOut>({
        kind: "create",
        method: "POST",
        path: `/calendar/accounts/${encodeURIComponent(account.id)}/sync`,
        body: {},
        idempotencyKey,
      }),
    onSuccess: () => {
      onMessage(`Syncing ${account.google_email}.`);
    },
    onError: (error) => {
      onMessage(problemText(error, "The sync could not start."));
    },
  });

  const selected = new Set(account.selected_calendar_ids);
  const toggle = (calendarId: string, on: boolean) => {
    onMessage(null);
    const next = account.calendars
      .map((c) => c.id)
      .filter((id) => (id === calendarId ? on : selected.has(id)));
    choose.mutate({ selected: next });
  };

  const reauth = account.status === "needs_reauth";
  return (
    <div role="group" aria-label={account.google_email} className={CARD}>
      <p className="font-medium [overflow-wrap:anywhere]">
        {account.google_email}
      </p>
      {reauth ? (
        <p className="text-sm text-danger">Needs reauth</p>
      ) : (
        <p className={HINT}>
          {account.last_sync_at
            ? `Last synced ${new Date(account.last_sync_at).toLocaleString()}`
            : "Not synced yet"}
        </p>
      )}
      <fieldset className="flex flex-col gap-1">
        <legend className="text-sm font-medium">Calendars to sync</legend>
        {account.calendars.map((calendar) => (
          <label
            key={calendar.id}
            className="flex min-h-11 items-center gap-2 [overflow-wrap:anywhere]"
          >
            <input
              type="checkbox"
              className="size-5"
              checked={selected.has(calendar.id)}
              disabled={choose.isPending}
              onChange={(e) => {
                toggle(calendar.id, e.target.checked);
              }}
            />
            {calendar.summary}
          </label>
        ))}
      </fieldset>
      <div className="flex flex-wrap gap-2">
        {reauth ? (
          <button
            type="button"
            className={BUTTON}
            disabled={busy}
            onClick={onReconnect}
          >
            Reconnect
          </button>
        ) : (
          <button
            type="button"
            className={SECONDARY}
            disabled={syncNow.isPending}
            onClick={() => {
              onMessage(null);
              syncNow.mutate({});
            }}
          >
            Sync now
          </button>
        )}
      </div>
    </div>
  );
}

function OAuthClientForm() {
  const queryClient = useQueryClient();
  const loaded = useQuery(calendarOAuthClientQuery());
  const ids = { clientId: useId(), secret: useId() };
  const [clientId, setClientId] = useState<string | null>(null);
  const [secret, setSecret] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  const section = loaded.data;
  const storedId =
    typeof section?.values.client_id === "string"
      ? section.values.client_id
      : "";
  const secretSet = section?.secrets_set.includes("client_secret") ?? false;

  const save = useWrite<
    { values: Record<string, string>; idempotencyKey?: string },
    SettingSectionOut
  >({
    mutationFn: ({ values, idempotencyKey }) =>
      section?.version == null
        ? apiWrite<SettingSectionOut>({
            kind: "create",
            method: "PUT",
            path: "/settings/calendar.google",
            body: { values, version: null },
            idempotencyKey,
          })
        : apiWrite<SettingSectionOut>({
            kind: "update",
            method: "PUT",
            path: "/settings/calendar.google",
            body: { values },
            version: section.version,
            idempotencyKey,
          }),
    onSuccess: (saved) => {
      queryClient.setQueryData(calendarOAuthClientQuery().queryKey, saved);
      setSecret("");
      setClientId(null);
      setMessage("Saved.");
    },
    onError: (error) => {
      setMessage(problemText(error, "The OAuth client could not be saved."));
    },
  });

  return (
    <form
      className={`${CARD} max-w-xl`}
      onSubmit={(e) => {
        e.preventDefault();
        const values: Record<string, string> = {
          client_id: (clientId ?? storedId).trim(),
        };
        if (secret) values.client_secret = secret;
        save.mutate({ values });
      }}
    >
      <h3 className="font-medium">Google OAuth client</h3>
      <p className={HINT}>
        The web client from your Google Cloud project, with the callback URL
        /v1/calendar/oauth/callback on this server.
      </p>
      <label htmlFor={ids.clientId} className={LABEL}>
        Client ID
      </label>
      <input
        id={ids.clientId}
        className={INPUT}
        autoComplete="off"
        value={clientId ?? storedId}
        onChange={(e) => {
          setClientId(e.target.value);
        }}
      />
      <label htmlFor={ids.secret} className={LABEL}>
        Client secret
      </label>
      <input
        id={ids.secret}
        type="password"
        className={INPUT}
        autoComplete="new-password"
        placeholder={secretSet ? "Set (leave blank to keep)" : ""}
        value={secret}
        onChange={(e) => {
          setSecret(e.target.value);
        }}
      />
      <p role="status" className={HINT}>
        {message}
      </p>
      <div>
        <button type="submit" className={SECONDARY} disabled={save.isPending}>
          Save OAuth client
        </button>
      </div>
    </form>
  );
}

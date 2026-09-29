// The reads each Settings section makes (P0-26), shared by the sections and the route's
// loader so a prefetch fills exactly the query the section then reads.
import {
  authGetAccountOptions,
  authListKeysOptions,
  authListSessionsOptions,
  deadLettersGetDeadLettersOptions,
  settingsGetWorkspaceSettingsOptions,
} from "../../api/@tanstack/react-query.gen";

export const DEAD_LETTER_STATUSES = [
  "open",
  "retrying",
  "resolved",
  "discarded",
] as const;
export type DeadLetterStatus = (typeof DEAD_LETTER_STATUSES)[number];

export const accountQuery = () => authGetAccountOptions();
export const sessionsQuery = () =>
  authListSessionsOptions({ query: { limit: 50 } });
// The same options ApiKeys reads (P0-14), so a live `api_key` message refreshes both.
export const keysQuery = () => authListKeysOptions({ query: { limit: 200 } });
export const deadLettersQuery = (status: DeadLetterStatus = "open") =>
  deadLettersGetDeadLettersOptions({ query: { status, limit: 50 } });
export const workspaceQuery = () => settingsGetWorkspaceSettingsOptions();

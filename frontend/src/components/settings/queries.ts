// The reads each Settings section makes (P0-26), shared by the sections and the route's
// loader so a prefetch fills exactly the query the section then reads.
import {
  agentsGetProfileToolsOptions,
  agentsListProfilesOptions,
  agentsListRunnersOptions,
  authGetAccountOptions,
  authListKeysOptions,
  authListSessionsOptions,
  calendarListAccountsOptions,
  deadLettersGetDeadLettersOptions,
  decisionsGetCalibrationOptions,
  knowledgeListLocationsOptions,
  settingsGetSectionOptions,
  settingsGetWorkingHoursOptions,
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
export const workingHoursQuery = () => settingsGetWorkingHoursOptions();
export const calendarAccountsQuery = () => calendarListAccountsOptions();
export const calendarOAuthClientQuery = () =>
  settingsGetSectionOptions({ path: { section: "calendar.google" } });
export const storageQuery = () => knowledgeListLocationsOptions();
// Settings > Agents (P1-04): the same options a live `runner` or `agent_profile`
// message refreshes.
export const runnersQuery = () =>
  agentsListRunnersOptions({ query: { limit: 100 } });
export const profilesQuery = () =>
  agentsListProfilesOptions({ query: { limit: 100 } });
// The project's own agent (P1-06, the project header): the list filtered on the server.
export const projectProfileQuery = (projectId: string) =>
  agentsListProfilesOptions({ query: { project_id: projectId, limit: 1 } });
// Settings > Agents > a profile's tools (P2-10, FR-5.12): read-only.
export const profileToolsQuery = (profileId: string) =>
  agentsGetProfileToolsOptions({ path: { id: profileId } });
// Settings > Calibration (P3-08, FR-11.5): every decision point's threshold and evidence.
export const calibrationQuery = () => decisionsGetCalibrationOptions();

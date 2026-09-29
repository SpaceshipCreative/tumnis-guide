// Which cached queries a live message makes stale (P0-22, ADR-0004, R-05, R-19). Names
// are generated operation IDs (`<tag>_<function>` -> `tagFunction`); `details` match only
// when the message's id is in the query's path, `lists` always match. Every generated
// query op is here or in NOT_LIVE (T-P0-22-11): a WP that adds a GET adds it here.
export type LiveEntity =
  "task" | "project" | "review_item" | "settings" | "api_key" | "dead_letter";

export const LIVE_MAP: Record<
  LiveEntity,
  { details: string[]; lists: string[] }
> = {
  // tasksGetTask; tasksListTasks, tasksGetBoard, searchSearch, searchTypeaheadTasks (P0-18, P0-20)
  task: { details: [], lists: [] },
  // searchTypeaheadProjects joins the lists with P0-20.
  project: {
    details: ["projectsGetProject"],
    lists: ["projectsListProjects"],
  },
  // tasksGetReviewCount (P0-18)
  review_item: { details: [], lists: [] },
  // The workspace settings have no id in their path: any settings message refreshes them.
  settings: { details: [], lists: ["settingsGetWorkspaceSettings"] },
  // Created, rotated and revoked keys (P0-14): the Settings list refreshes.
  api_key: { details: [], lists: ["authListKeys"] },
  dead_letter: { details: [], lists: ["deadLettersGetDeadLetters"] },
};

export const NOT_LIVE = [
  "healthLive",
  "healthReady",
  "auditListAudit",
  "auditExportAuditCsv",
  "usageGetUsage",
  "authListSessions",
] as const;

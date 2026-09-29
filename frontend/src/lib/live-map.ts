// Which cached queries a live message makes stale (P0-22, ADR-0004, R-05, R-19). Names
// are generated operation IDs (`<tag>_<function>` -> `tagFunction`); `details` match only
// when the message's id is in the query's path, `lists` always match. Every generated
// query op is here or in NOT_LIVE (T-P0-22-11): a WP that adds a GET adds it here.
export type LiveEntity =
  | "task"
  | "project"
  | "review_item"
  | "settings"
  | "api_key"
  | "dead_letter"
  | "calendar_account";

export const LIVE_MAP: Record<
  LiveEntity,
  { details: string[]; lists: string[] }
> = {
  // Any task change refreshes the lists and every board (a card may move between boards'
  // columns, a subtask onto its parent's checklist), and search results (P0-20). A task's
  // recurrence rule and the project's recurrence list change with its task messages (P0-19).
  task: {
    details: ["tasksGetTask", "tasksGetRecurrence"],
    lists: [
      "tasksListTasks",
      "tasksGetBoard",
      "tasksListRecurrence",
      "searchSearch",
      "searchTypeaheadTasks",
    ],
  },
  // A project's board and columns carry its id in their path: column edits and a new card
  // threshold (FR-3.8) refresh them; any project change refreshes search results (P0-20).
  // The Coolify poll announces a linked project when its deploy status changes, and a link
  // edit changes which apps a project shows (P2-14).
  project: {
    details: ["projectsGetProject", "tasksGetBoard", "tasksGetColumns"],
    lists: [
      "projectsListProjects",
      "searchSearch",
      "searchTypeaheadProjects",
      "coolifyListDeployStatus",
    ],
  },
  // The review badge (P0-18); the review queue joins with P1-13.
  review_item: { details: [], lists: ["tasksGetReviewCount"] },
  // The workspace settings have no id in their path: any settings message refreshes them,
  // the sections and the module switches too (P0-26).
  settings: {
    details: [],
    lists: [
      "settingsGetWorkspaceSettings",
      "settingsGetSection",
      "settingsListModules",
    ],
  },
  // Created, rotated and revoked keys (P0-14): the Settings list refreshes.
  api_key: { details: [], lists: ["authListKeys"] },
  dead_letter: { details: [], lists: ["deadLettersGetDeadLetters"] },
  // Connected Google accounts (P1-09): a sync or a revoked grant refreshes the list.
  calendar_account: { details: [], lists: ["calendarListAccounts"] },
};

export const NOT_LIVE = [
  "healthLive",
  "healthReady",
  "auditListAudit",
  "auditExportAuditCsv",
  "usageGetUsage",
  "authListSessions",
  "authGetAccount",
  // The review kinds are registered at startup; they change only with a deploy.
  "tasksListReviewKinds",
  // The OAuth start mints a fresh consent URL per call; the callback is a browser redirect.
  "calendarOauthStart",
  "calendarOauthCallback",
  // Storage locations change only from the Settings screen, which refetches after each write.
  "knowledgeListLocations",
] as const;

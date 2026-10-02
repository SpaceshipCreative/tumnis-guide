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
  | "calendar_account"
  | "runner"
  | "agent_profile"
  | "run"
  | "plan"
  | "focus"
  | "knowledge"
  | "agent_pause"
  | "speech_clip";

export const LIVE_MAP: Record<
  LiveEntity,
  { details: string[]; lists: string[] }
> = {
  // Any task change refreshes the lists and every board (a card may move between boards'
  // columns, a subtask onto its parent's checklist), and search results (P0-20). A task's
  // recurrence rule and the project's recurrence list change with its task messages (P0-19).
  task: {
    details: [
      "tasksGetTask",
      "tasksListComments",
      "tasksGetRecurrence",
      "tasksListPullRequests",
      "agentsGetTaskPacket", // the task's packet (P2-02) changes with its task
      "tasksGetUnattended", // its unattended queue flag (P4-04), consumed when it runs
    ],
    lists: [
      "tasksListTasks",
      "tasksGetBoard",
      "tasksListRecurrence",
      "searchSearch",
      "searchTypeaheadTasks",
      "planningGetProjectWeek", // due dates and the tasks to schedule (P1-12)
      "planningGetPlan", // an item's live status and blocked flag (P1-11)
      "planningGetAlternates",
      "planningGetDaySummary", // what shipped and what rolls over (P1-18)
    ],
  },
  // A project's board, columns and agent context (P2-01) carry its id in their path:
  // column edits and a new card threshold (FR-3.8) refresh them, a saved brief its Brief
  // rail section (P0-24); any project change refreshes search results (P0-20). The Coolify
  // poll announces a linked project when its deploy status changes, and a link edit
  // changes which apps a project shows (P2-14) and which calendar events match the
  // project's week (P1-12).
  project: {
    details: [
      "projectsGetProject",
      "projectsGetProjectContext",
      "tasksGetBoard",
      "tasksGetColumns",
      "knowledgeGetBrief",
      "projectsGetPolicy", // a saved approval policy (the policy editor, P2-05)
    ],
    lists: [
      "projectsListProjects",
      "searchSearch",
      "searchTypeaheadProjects",
      "coolifyListDeployStatus",
      "planningGetProjectWeek", // link edits change which events match (P1-12)
      // P1-17: a project item's knowledge write marks its project changed (the Knowledge
      // rail); a workspace item's sends `knowledge` instead (below).
      "knowledgeListDocuments",
      "knowledgeGetQuota",
      "knowledgeSearch",
    ],
  },
  // The review badge (P0-18) and the review queue (P1-13).
  review_item: {
    details: [],
    lists: [
      "tasksGetReviewCount",
      "tasksListReview",
      "tasksListInbox", // a project's open proposals (P2-17)
    ],
  },
  // The workspace settings have no id in their path: any settings message refreshes them,
  // the sections and the module switches too (P0-26).
  settings: {
    details: [],
    lists: [
      "settingsGetWorkspaceSettings",
      "settingsGetSection",
      "settingsListModules",
      "settingsGetWorkingHours",
      "planningGetDayCalendar",
      "planningGetProjectWeek",
    ],
  },
  // Created, rotated and revoked keys (P0-14): the Settings list refreshes.
  api_key: { details: [], lists: ["authListKeys"] },
  dead_letter: { details: [], lists: ["deadLettersGetDeadLetters"] },
  // Connected Google accounts (P1-09): a sync or a revoked grant refreshes the list, and
  // the day's events and free blocks (P1-10) come from the same synced events.
  calendar_account: {
    details: [],
    lists: [
      "calendarListAccounts",
      "planningGetDayCalendar",
      "planningGetProjectWeek",
    ],
  },
  // Runners register, heartbeat, go offline and get new tokens; profiles get health
  // checks (P1-04): the Settings agents section refreshes, and a profile's tools (P2-10).
  runner: { details: [], lists: ["agentsListRunners"] },
  agent_profile: {
    details: ["agentsGetProfileTools"],
    lists: ["agentsListProfiles"],
  },
  // A run's status and its log (P2-04): a run message refreshes its header and events
  // page (the run view pages on from its cursor). A run's status also moves it between
  // the dashboard feed's groups and adds to its project's Activity (P2-17).
  run: {
    details: ["agentsGetRun", "agentsListRunEvents"],
    lists: ["agentsGetAgentFeed", "agentsListActivity"],
  },
  // A pause or resume (P2-09): the kill switch and every project's pause control refresh.
  agent_pause: { details: [], lists: ["agentsGetPauses"] },
  // A workspace knowledge base item (no project) written, trashed or restored (P1-17):
  // its message carries the document id, and every knowledge list, search and quota
  // includes the workspace items, so they all refresh.
  knowledge: {
    details: [],
    lists: ["knowledgeListDocuments", "knowledgeGetQuota", "knowledgeSearch"],
  },
  // A plan published, superseded or acted on (P1-11): its path names the day, not the
  // plan, so every plan message refreshes the day's plan, its alternates and the week.
  plan: {
    details: [],
    lists: [
      "planningGetPlan",
      "planningGetAlternates",
      "planningGetProjectWeek",
    ],
  },
  // A focus event fired or answered, a session started or ended, or the level changed
  // (P2-15): the focus bar's one read refreshes.
  focus: {
    details: [],
    lists: ["focusGetCurrent"],
  },
  // A spoken focus message's clip made by the server engine (P4-03): its message carries
  // the focus event's id, and the focus bar's read gains the clip id.
  speech_clip: { details: [], lists: ["focusGetCurrent"] },
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
  // An upload's status is polled until extraction settles (P1-16; no document live message
  // yet), and a file is a download, never a cached query.
  "knowledgeGetDocument",
  "knowledgeGetFile",
  // A document's versions are read on demand when its history is opened (P1-17).
  "knowledgeListVersions",
  // Calibration is evidence read on demand (P3-08); the Calibration screen refetches after
  // its own threshold edit.
  "decisionsGetCalibration",
  // The local metrics are a summary over days, read when Settings > Metrics opens (P1-18).
  "planningGetMetricsSummary",
  // The digests are an agent's read-once feed (P2-03): each read moves the caller's
  // cursor, so the UI never caches or refetches them.
  "agentsGetProjectDigest",
  "agentsGetWorkspaceDigest",
  // The VAPID public key is read once, when "Enable push" is pressed (P4-05); it never
  // changes for a workspace.
  "notificationsGetVapidPublicKey",
  // A clip is audio the voice engine plays from its URL (P4-03), never a cached query.
  "speechGetClip",
  // The master's long poll on a delegation (P2-06): an agent's call that waits up to ten
  // minutes, never a cached query of the app.
  "agentsWaitForTask",
  // Connections (P3-02) send no live message: Settings > Connections refetches its list
  // every minute while open and after each write. The providers are registered at
  // startup; the sign-in URL is polled once per connect; the callback is a browser
  // redirect.
  "connectionsListConnections",
  "connectionsGetConnection",
  "connectionsListProviders",
  "connectionsOauthUrl",
  "connectionsOauthCallback",
  // The unattended window (P4-04) changes only from Settings or a project's Schedule rail,
  // and each writes the saved answer into its own cache.
  "planningGetUnattendedWindow",
] as const;

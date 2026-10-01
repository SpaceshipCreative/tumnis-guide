// The focus session as the bar reflects it (P2-15, FR-10.2, FR-10.4, FR-10.9; XState v5
// `setup`, delayed transitions). The server owns cadence and firing; this machine only
// mirrors what the bar shows: a block about to start, an active task with its timer, a
// check-in waiting for a one-tap answer, a snooze, a stuck answer. Replies post through
// `postResponse` and "less of this" through `postLess`, both replaced with
// `machine.provide()` in the app and the tests (R-38: every guard, action and delay the
// config names is declared here).
import { assign, setup } from "xstate";

export type FocusLevel = "quiet" | "nudge" | "coach" | "guardrail";
export type FocusEventKind =
  | "block_start"
  | "not_started"
  | "check_in_due"
  | "switched"
  | "stuck"
  | "block_end"
  | "day_end";

export const SNOOZE_MS = 15 * 60 * 1000; // FR-10.4: a snooze is 15 minutes

export interface FocusContext {
  taskId?: string;
  level: FocusLevel;
  rule?: string;
  message?: string;
  startedAt?: number;
  // P4-01: the level in force is Guardrail (detours are captured), and the task a
  // captured detour offers to return to.
  guardrail?: boolean;
  returnToTaskId?: string;
}

export type FocusSessionEvent =
  | {
      type: "FOCUS_EVENT";
      kind: FocusEventKind;
      taskId: string;
      level: FocusLevel;
      rule: string;
      message: string;
    }
  | { type: "TASK_STARTED"; taskId: string; at: number }
  | { type: "TASK_LEFT"; taskId: string }
  | { type: "STILL_ON_IT" }
  | { type: "SWITCHED"; toTaskId: string }
  | { type: "STUCK" }
  | { type: "SNOOZE" }
  | { type: "LESS_OF_THIS" }
  | { type: "DISMISS" }
  // P4-01 (Guardrail): the level in force, a switch to something not in Today, the
  // server's return question, and its answers.
  | { type: "LEVEL"; level: FocusLevel }
  | { type: "SWITCH_DETOUR"; title: string; projectId: string }
  | { type: "RETURN_PROMPT"; detourTaskId: string; returnToTaskId: string }
  | { type: "RETURN" }
  | { type: "STAY" };

export const focusSession = setup({
  types: {
    context: {} as FocusContext,
    events: {} as FocusSessionEvent,
  },
  guards: {
    isBlockStart: ({ event }) =>
      event.type === "FOCUS_EVENT" && event.kind === "block_start",
    isNotStarted: ({ event }) =>
      event.type === "FOCUS_EVENT" && event.kind === "not_started",
    isCheckInOrSwitched: ({ event }) =>
      event.type === "FOCUS_EVENT" &&
      (event.kind === "check_in_due" || event.kind === "switched"),
  },
  actions: {
    show: assign(({ event }) =>
      event.type === "FOCUS_EVENT"
        ? {
            taskId: event.taskId,
            level: event.level,
            rule: event.rule,
            message: event.message,
          }
        : {},
    ),
    startTimer: assign(({ event }) =>
      event.type === "TASK_STARTED"
        ? { taskId: event.taskId, startedAt: event.at }
        : {},
    ),
    retarget: assign(({ event }) =>
      event.type === "SWITCHED" ? { taskId: event.toTaskId } : {},
    ),
    // Replaced through machine.provide() in the app and the tests.
    postResponse: () => undefined,
    postLess: () => undefined,
  },
  delays: { snooze: SNOOZE_MS },
}).createMachine({
  id: "focusSession",
  initial: "idle",
  context: { level: "quiet" },
  states: {
    idle: {
      on: {
        FOCUS_EVENT: [
          { guard: "isBlockStart", target: "blockPending", actions: "show" },
          { guard: "isNotStarted", target: "checkIn", actions: "show" },
        ],
        TASK_STARTED: { target: "active", actions: "startTimer" },
      },
    },
    blockPending: {
      on: {
        TASK_STARTED: { target: "active", actions: "startTimer" },
        FOCUS_EVENT: {
          guard: "isNotStarted",
          target: "checkIn",
          actions: "show",
        },
        DISMISS: "idle",
      },
    },
    active: {
      on: {
        FOCUS_EVENT: {
          guard: "isCheckInOrSwitched",
          target: "checkIn",
          actions: "show",
        },
        TASK_LEFT: "idle",
      },
    },
    checkIn: {
      on: {
        STILL_ON_IT: { target: "active", actions: "postResponse" },
        SWITCHED: { target: "active", actions: ["postResponse", "retarget"] },
        STUCK: { target: "stuck", actions: "postResponse" },
        SNOOZE: { target: "snoozed", actions: "postResponse" },
        LESS_OF_THIS: { actions: "postLess" },
        TASK_LEFT: "idle",
        DISMISS: "active",
      },
    },
    snoozed: {
      after: { snooze: "active" },
      on: {
        FOCUS_EVENT: {
          guard: "isCheckInOrSwitched",
          target: "checkIn",
          actions: "show",
        },
        TASK_LEFT: "idle",
      },
    },
    // Beyond the plan's config: the session goes on after a stuck answer, so its next
    // check-in or a switch opens a check-in here too, as from active and snoozed.
    stuck: {
      on: {
        FOCUS_EVENT: {
          guard: "isCheckInOrSwitched",
          target: "checkIn",
          actions: "show",
        },
        TASK_STARTED: { target: "active", actions: "startTimer" },
        TASK_LEFT: "idle",
        DISMISS: "active",
      },
    },
  },
});

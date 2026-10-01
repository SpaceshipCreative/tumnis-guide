// The focus session as the bar reflects it (P2-15): stub until implemented.
import { setup } from "xstate";

export type FocusLevel = "quiet" | "nudge" | "coach" | "guardrail";

export type FocusSessionEvent =
  | {
      type: "FOCUS_EVENT";
      kind: string;
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
  | { type: "DISMISS" };

export const focusSession = setup({
  types: {
    context: {} as { taskId?: string; level: FocusLevel; startedAt?: number },
    events: {} as FocusSessionEvent,
  },
  actions: { postResponse: () => {}, postLess: () => {} },
}).createMachine({
  id: "focusSession",
  initial: "idle",
  context: { level: "quiet" },
  states: {
    idle: {
      on: {
        STILL_ON_IT: { actions: "postResponse" },
        LESS_OF_THIS: { actions: "postLess" },
      },
    },
  },
});

// The focusSession machine after a stuck answer (P2-15, FR-10.4): the session goes on,
// so its next check-in or a switch opens a check-in again; a block or a stuck event
// does not.
import { expect, test } from "vitest";
import { createActor } from "xstate";

import {
  focusSession,
  type FocusEventKind,
  type FocusSessionEvent,
} from "./focusSession";

const TASK = "01890000-0000-7000-8000-0000000000a1";

function event(kind: FocusEventKind): FocusSessionEvent {
  return {
    type: "FOCUS_EVENT",
    kind,
    taskId: TASK,
    level: "coach",
    rule: `Coach · ${kind}`,
    message: `${kind} message`,
  };
}

function stuckActor() {
  const actor = createActor(focusSession);
  actor.start();
  actor.send({ type: "TASK_STARTED", taskId: TASK, at: 1 });
  actor.send(event("check_in_due"));
  actor.send({ type: "STUCK" });
  return actor;
}

test("[P2-15][FR-10.4] a check-in after a stuck answer opens a check-in", () => {
  const actor = stuckActor();
  expect(actor.getSnapshot().value).toBe("stuck");
  actor.send(event("check_in_due"));
  expect(actor.getSnapshot().value).toBe("checkIn");
  expect(actor.getSnapshot().context.rule).toBe("Coach · check_in_due");

  const switched = stuckActor();
  switched.send(event("switched"));
  expect(switched.getSnapshot().value).toBe("checkIn");

  const other = stuckActor();
  other.send(event("stuck"));
  other.send(event("block_start"));
  expect(other.getSnapshot().value).toBe("stuck");
});

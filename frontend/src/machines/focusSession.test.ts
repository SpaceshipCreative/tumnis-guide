// The focusSession machine (P2-15, FR-10.4): driven by events only, no rendering. Every
// transition of the plan's machine, the replies' posts (provided spies), and a snooze that
// returns to active after 15 minutes on fake timers. machines.test.ts checks R-38 (every
// guard, action and delay named in the config is declared in setup) for this file too.
import { afterEach, expect, test, vi } from "vitest";
import { createActor } from "xstate";

import { focusSession, type FocusSessionEvent } from "./focusSession";

const TASK = "01890000-0000-7000-8000-0000000000a1";
const OTHER = "01890000-0000-7000-8000-0000000000a2";

function focusEvent(kind: string): FocusSessionEvent {
  return {
    type: "FOCUS_EVENT",
    kind: kind as never,
    taskId: TASK,
    level: "coach",
    rule: `Coach · ${kind}`,
    message: `${kind} message`,
  };
}

function start() {
  const postResponse = vi.fn();
  const postLess = vi.fn();
  const actor = createActor(
    focusSession.provide({ actions: { postResponse, postLess } }),
  );
  actor.start();
  const state = () => actor.getSnapshot().value;
  return { actor, state, postResponse, postLess };
}

function toCheckIn(): ReturnType<typeof start> {
  const run = start();
  run.actor.send({ type: "TASK_STARTED", taskId: TASK, at: 1000 });
  run.actor.send(focusEvent("check_in_due"));
  return run;
}

afterEach(() => {
  vi.useRealTimers();
});

test("[P2-15][FR-10.4] T-P2-15-16 replies move the machine as specified", () => {
  vi.useFakeTimers();

  // idle: block_start shows the block, not_started goes straight to a check-in, starting
  // a task starts the timer; other events change nothing.
  const idle = start();
  expect(idle.state()).toBe("idle");
  idle.actor.send(focusEvent("check_in_due"));
  expect(idle.state()).toBe("idle");
  idle.actor.send(focusEvent("block_start"));
  expect(idle.state()).toBe("blockPending");
  expect(idle.actor.getSnapshot().context).toMatchObject({
    taskId: TASK,
    level: "coach",
    rule: "Coach · block_start",
    message: "block_start message",
  });
  idle.actor.send({ type: "DISMISS" });
  expect(idle.state()).toBe("idle");
  idle.actor.send(focusEvent("not_started"));
  expect(idle.state()).toBe("checkIn");
  const started = start();
  started.actor.send({ type: "TASK_STARTED", taskId: TASK, at: 42 });
  expect(started.state()).toBe("active");
  expect(started.actor.getSnapshot().context.startedAt).toBe(42);

  // blockPending: starting the task, not_started, dismiss.
  const pending = start();
  pending.actor.send(focusEvent("block_start"));
  pending.actor.send({ type: "TASK_STARTED", taskId: TASK, at: 7 });
  expect(pending.state()).toBe("active");
  const late = start();
  late.actor.send(focusEvent("block_start"));
  late.actor.send(focusEvent("not_started"));
  expect(late.state()).toBe("checkIn");

  // active: check_in_due and switched open a check-in; block_start does not; leaving the
  // task goes idle.
  const active = start();
  active.actor.send({ type: "TASK_STARTED", taskId: TASK, at: 1 });
  active.actor.send(focusEvent("block_start"));
  expect(active.state()).toBe("active");
  active.actor.send(focusEvent("switched"));
  expect(active.state()).toBe("checkIn");
  active.actor.send({ type: "DISMISS" });
  expect(active.state()).toBe("active");
  active.actor.send({ type: "TASK_LEFT", taskId: TASK });
  expect(active.state()).toBe("idle");

  // checkIn replies: each posts the answer, except "less of this", which posts less and
  // stays.
  const still = toCheckIn();
  still.actor.send({ type: "STILL_ON_IT" });
  expect(still.state()).toBe("active");
  expect(still.postResponse).toHaveBeenCalledTimes(1);

  const switched = toCheckIn();
  switched.actor.send({ type: "SWITCHED", toTaskId: OTHER });
  expect(switched.state()).toBe("active");
  expect(switched.postResponse).toHaveBeenCalledTimes(1);
  expect(switched.actor.getSnapshot().context.taskId).toBe(OTHER);

  const stuck = toCheckIn();
  stuck.actor.send({ type: "STUCK" });
  expect(stuck.state()).toBe("stuck");
  expect(stuck.postResponse).toHaveBeenCalledTimes(1);
  stuck.actor.send({ type: "DISMISS" });
  expect(stuck.state()).toBe("active");
  const stuckAgain = toCheckIn();
  stuckAgain.actor.send({ type: "STUCK" });
  stuckAgain.actor.send({ type: "TASK_STARTED", taskId: OTHER, at: 9 });
  expect(stuckAgain.state()).toBe("active");
  expect(stuckAgain.actor.getSnapshot().context.taskId).toBe(OTHER);
  const stuckLeft = toCheckIn();
  stuckLeft.actor.send({ type: "STUCK" });
  stuckLeft.actor.send({ type: "TASK_LEFT", taskId: TASK });
  expect(stuckLeft.state()).toBe("idle");

  const less = toCheckIn();
  less.actor.send({ type: "LESS_OF_THIS" });
  expect(less.state()).toBe("checkIn");
  expect(less.postLess).toHaveBeenCalledTimes(1);
  expect(less.postResponse).not.toHaveBeenCalled();

  const left = toCheckIn();
  left.actor.send({ type: "TASK_LEFT", taskId: TASK });
  expect(left.state()).toBe("idle");

  // snoozed: back to active after 15 minutes, not before; a check-in or switch while
  // snoozed opens a check-in; leaving the task goes idle.
  const snooze = toCheckIn();
  snooze.actor.send({ type: "SNOOZE" });
  expect(snooze.state()).toBe("snoozed");
  expect(snooze.postResponse).toHaveBeenCalledTimes(1);
  vi.advanceTimersByTime(15 * 60 * 1000 - 1);
  expect(snooze.state()).toBe("snoozed");
  vi.advanceTimersByTime(1);
  expect(snooze.state()).toBe("active");

  const interrupted = toCheckIn();
  interrupted.actor.send({ type: "SNOOZE" });
  interrupted.actor.send(focusEvent("check_in_due"));
  expect(interrupted.state()).toBe("checkIn");
  const snoozedLeft = toCheckIn();
  snoozedLeft.actor.send({ type: "SNOOZE" });
  snoozedLeft.actor.send({ type: "TASK_LEFT", taskId: TASK });
  expect(snoozedLeft.state()).toBe("idle");
});

const DETOUR = "01890000-0000-7000-8000-0000000000a3";

function toGuardrailActive() {
  const postDetour = vi.fn();
  const postReturn = vi.fn();
  const postStay = vi.fn();
  const implementations = {
    actions: { postDetour, postReturn, postStay },
  } as Parameters<typeof focusSession.provide>[0];
  const actor = createActor(focusSession.provide(implementations));
  actor.start();
  actor.send({ type: "LEVEL", level: "guardrail" });
  actor.send({ type: "TASK_STARTED", taskId: TASK, at: 1000 });
  const state = () => actor.getSnapshot().value;
  return { actor, state, postDetour, postReturn, postStay };
}

const SWITCH_DETOUR: FocusSessionEvent = {
  type: "SWITCH_DETOUR",
  title: "Call the bank about the card",
  projectId: OTHER,
};
const RETURN_PROMPT: FocusSessionEvent = {
  type: "RETURN_PROMPT",
  detourTaskId: DETOUR,
  returnToTaskId: TASK,
};

test.fails("[P4-01][FR-10.6] T-P4-01-11 detour then return path", () => {
  vi.useFakeTimers();

  // Guardrail: a switch to something else opens the detour (and posts it); the server's
  // answer asks the return question with the detour as the current task.
  const ret = toGuardrailActive();
  expect(ret.actor.getSnapshot().context.guardrail).toBe(true);
  ret.actor.send(SWITCH_DETOUR);
  expect(ret.state()).toBe("detour");
  expect(ret.postDetour).toHaveBeenCalledTimes(1);
  ret.actor.send(RETURN_PROMPT);
  expect(ret.state()).toBe("returnPrompt");
  expect(ret.actor.getSnapshot().context).toMatchObject({
    taskId: DETOUR,
    returnToTaskId: TASK,
  });
  // Return: back on the previous task.
  ret.actor.send({ type: "RETURN" });
  expect(ret.state()).toBe("active");
  expect(ret.postReturn).toHaveBeenCalledTimes(1);
  expect(ret.postStay).not.toHaveBeenCalled();
  expect(ret.actor.getSnapshot().context.taskId).toBe(TASK);

  // Stay: on the detour.
  const stay = toGuardrailActive();
  stay.actor.send(SWITCH_DETOUR);
  stay.actor.send(RETURN_PROMPT);
  stay.actor.send({ type: "STAY" });
  expect(stay.state()).toBe("active");
  expect(stay.postStay).toHaveBeenCalledTimes(1);
  expect(stay.postReturn).not.toHaveBeenCalled();
  expect(stay.actor.getSnapshot().context.taskId).toBe(DETOUR);

  // Unanswered: the question dismisses itself after 2 minutes and counts as Stay
  // (FR-10.9: nothing waits on an answer).
  const quiet = toGuardrailActive();
  quiet.actor.send(SWITCH_DETOUR);
  quiet.actor.send(RETURN_PROMPT);
  vi.advanceTimersByTime(2 * 60 * 1000 - 1);
  expect(quiet.state()).toBe("returnPrompt");
  vi.advanceTimersByTime(1);
  expect(quiet.state()).toBe("active");
  expect(quiet.postStay).toHaveBeenCalledTimes(1);

  // The capture failed (no question came back): back to the task the bar shows.
  const failed = toGuardrailActive();
  failed.actor.send(SWITCH_DETOUR);
  failed.actor.send({ type: "DISMISS" });
  expect(failed.state()).toBe("active");
  expect(failed.actor.getSnapshot().context.taskId).toBe(TASK);

  // A question already open when the bar loads (the server's state) shows at once.
  const reloaded = toGuardrailActive();
  reloaded.actor.send(RETURN_PROMPT);
  expect(reloaded.state()).toBe("returnPrompt");

  // Below Guardrail a detour is not captured.
  const coach = toGuardrailActive();
  coach.actor.send({ type: "LEVEL", level: "coach" });
  expect(coach.actor.getSnapshot().context.guardrail).toBe(false);
  coach.actor.send(SWITCH_DETOUR);
  expect(coach.state()).toBe("active");
  expect(coach.postDetour).not.toHaveBeenCalled();
});

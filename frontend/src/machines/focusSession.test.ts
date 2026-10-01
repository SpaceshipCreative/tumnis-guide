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

test.fails(
  "[P2-15][FR-10.4] T-P2-15-16 replies move the machine as specified",
  () => {
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
  },
);

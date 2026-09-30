// The project's Calendar view (P1-12, FR-2.6): one week, Monday to Sunday in the workspace
// timezone, of the project's meetings, due dates and planned blocks over the free time
// (`GET /v1/plan/week/{monday}`), and the project's tasks left to schedule.
//
// Scheduling a task is one `PATCH /v1/plan/{day}/items/{task_id}` (`useScheduleBlock`,
// optimistic, rolled back when the server refuses). On a laptop the week is a grid: drag
// a card onto a free slot with the mouse or by touch (dnd-kit), or pick it up with Space,
// move across slots with the arrow keys (Up and Down within the day, Left and Right to the
// same time on another day) and drop it with Space. The drop targets are free-time slots
// computed here from the payload (free blocks minus planned blocks, on 15-minute marks),
// so a drop on busy time never sends a request; the server checks again for races. On the
// phone the week is a day list, and each task's Schedule action opens a slot picker.
import {
  DndContext,
  MouseSensor,
  pointerWithin,
  TouchSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import {
  keepPreviousData,
  useQuery,
  type QueryClient,
} from "@tanstack/react-query";
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { planningGetProjectWeekOptions } from "../../api/@tanstack/react-query.gen";
import type {
  PlanItemOut,
  TaskRefOut,
  WeekDayOut,
  WeekOut,
} from "../../api/types.gen";
import { zPlanItemOut } from "../../api/zod.gen";
import { apiWrite, ConflictError, useWrite } from "../../lib/fetch";
import { useIsLaptop } from "../../lib/media";
import { queryId } from "../../lib/task-cache";
import {
  addDays,
  blockFrom,
  clockTime,
  dayName,
  fitsIn,
  fittingStarts,
  minuteOfDay,
  shortDate,
  slotStarts,
  spanText,
  subtract,
  type Span,
} from "../../lib/time";

export interface CalendarViewProps {
  projectId: string;
  /** The ISO Monday of the week to show (`YYYY-MM-DD`). */
  week: string;
  onWeek: (monday: string) => void;
}

/** `GET /v1/plan/week/{monday}?project_id=`. */
export const weekQuery = (projectId: string, monday: string) =>
  planningGetProjectWeekOptions({
    path: { monday },
    query: { project_id: projectId },
  });

const BUSY = "That time is busy";
const NO_ROOM = "Not enough free time there";

/** A free slot: a 15-minute mark a task can start at. */
interface Slot {
  day: string;
  col: number; // 0 = Monday
  start: string;
  minute: number; // after local midnight
}

/** The free time left on a day: its free blocks minus the blocks planned on it. */
function openTime(day: WeekDayOut): Span[] {
  return subtract(day.free_blocks, day.planned);
}

// --- The one write ---------------------------------------------------------------------

interface ScheduleVars {
  task: TaskRefOut;
  day: string;
  block: Span;
  idempotencyKey?: string;
}

function planned(week: WeekOut, v: ScheduleVars): WeekOut {
  return {
    ...week,
    unscheduled: week.unscheduled.filter((t) => t.id !== v.task.id),
    days: week.days.map((d) =>
      d.day === v.day
        ? {
            ...d,
            planned: [
              ...d.planned,
              {
                task_id: v.task.id,
                title: v.task.title,
                start: v.block.start,
                end: v.block.end,
              },
            ].sort((a, b) => Date.parse(a.start) - Date.parse(b.start)),
          }
        : d,
    ),
  };
}

function useScheduleBlock(
  projectId: string,
  monday: string,
  say: (text: string) => void,
  timeZone: string,
) {
  const key = weekQuery(projectId, monday).queryKey;
  return useWrite<ScheduleVars, PlanItemOut, WeekOut | undefined>({
    mutationFn: (v) =>
      apiWrite({
        kind: "create",
        method: "PATCH",
        path: `/plan/${v.day}/items/${v.task.id}`,
        body: { block_start: v.block.start, block_end: v.block.end },
        idempotencyKey: v.idempotencyKey,
        schema: zPlanItemOut,
      }),
    onMutate: async (v, ctx) => {
      await ctx.client.cancelQueries({ queryKey: key });
      const previous = ctx.client.getQueryData(key);
      if (previous) ctx.client.setQueryData(key, planned(previous, v));
      return previous;
    },
    onSuccess: (_item, v) => {
      say(
        `Scheduled ${v.task.title}, ${dayName(v.day)}, ${spanText(v.block, timeZone)}`,
      );
    },
    onError: (error, _v, previous, ctx) => {
      if (previous) ctx.client.setQueryData(key, previous);
      say(
        error instanceof ConflictError
          ? "That time is no longer free"
          : "Could not schedule the task. Try again.",
      );
    },
    onSettled: (_d, _e, _v, _s, ctx) => refreshWeeks(ctx.client),
  });
}

/** Every cached week: a block planned here shows in another project's week too. */
function refreshWeeks(client: QueryClient): Promise<void> {
  return client.invalidateQueries({
    predicate: (q) => queryId(q.queryKey) === "planningGetProjectWeek",
  });
}

// --- The view --------------------------------------------------------------------------

export function CalendarView({ projectId, week, onWeek }: CalendarViewProps) {
  const headingId = useId();
  const laptop = useIsLaptop();
  // Another week keeps the one on screen as a placeholder while it loads, so the header
  // (and focus on Previous or Next week) stays put.
  const query = useQuery({
    ...weekQuery(projectId, week),
    placeholderData: keepPreviousData,
  });
  const [message, setMessage] = useState("");
  const timeZone = query.data?.timezone ?? "UTC";
  const schedule = useScheduleBlock(projectId, week, setMessage, timeZone);
  const [picking, setPicking] = useState<TaskRefOut | null>(null);
  // Slots before this are in the past; the view does not tick (a stale slot is refused by
  // the server as `block_in_past`).
  const [now] = useState(() => new Date());

  const send = (task: TaskRefOut, day: string, start: string) => {
    const days = query.data?.days ?? [];
    const found = days.find((d) => d.day === day);
    const block = blockFrom(start, task.estimate_minutes ?? 0);
    if (!found || !fitsIn(block, openTime(found))) {
      setMessage(NO_ROOM);
      return;
    }
    schedule.mutate({ task, day, block });
  };

  // The Calendar region appears with its week: nothing to show before the first load.
  if (query.isPending) {
    return <p className="text-muted">Loading the week…</p>;
  }

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id={headingId} className="text-lg font-semibold">
          Calendar
        </h2>
        <span className="text-sm text-muted">Week of {shortDate(week)}</span>
        <div className="ml-auto flex gap-1">
          <button
            type="button"
            className="min-h-11 rounded-md border border-border px-3 text-sm"
            onClick={() => {
              onWeek(addDays(week, -7));
            }}
          >
            Previous week
          </button>
          <button
            type="button"
            className="min-h-11 rounded-md border border-border px-3 text-sm"
            onClick={() => {
              onWeek(addDays(week, 7));
            }}
          >
            Next week
          </button>
        </div>
      </div>
      <p
        role="status"
        aria-label="Scheduling"
        aria-live="polite"
        className="min-h-5 text-sm text-muted"
      >
        {message}
      </p>
      {query.isPlaceholderData ? (
        <p className="text-muted">Loading the week…</p>
      ) : query.isError ? (
        <p role="alert" className="text-danger">
          The week could not be loaded.
        </p>
      ) : laptop ? (
        <WeekGrid
          week={query.data}
          now={now}
          say={setMessage}
          onDrop={send}
          onSchedule={setPicking}
        />
      ) : (
        <DayList week={query.data} onSchedule={setPicking} />
      )}
      {picking && query.data && !query.isPlaceholderData && (
        <SlotPicker
          task={picking}
          week={query.data}
          now={now}
          onPick={(day, start) => {
            setPicking(null);
            send(picking, day, start);
          }}
          onClose={() => {
            setPicking(null);
          }}
        />
      )}
    </section>
  );
}

// --- One day's contents, shared by the grid and the list ---------------------------------

interface Placed {
  col: number;
  minute: number;
  minutes: number;
}

function placed(span: Span, col: number, timeZone: string): Placed {
  return {
    col,
    minute: minuteOfDay(span.start, timeZone),
    minutes: Math.round(
      (Date.parse(span.end) - Date.parse(span.start)) / 60_000,
    ),
  };
}

/** Where a span sits in the day column's window, in % (the grid only). */
function position(span: Span, day: WeekDayOut) {
  const w = day.window;
  if (!w) return undefined;
  const from = Date.parse(w.start);
  const length = Date.parse(w.end) - from;
  const clamp = (x: number) => Math.min(Math.max(x, 0), 1);
  const top = clamp((Date.parse(span.start) - from) / length);
  const bottom = clamp((Date.parse(span.end) - from) / length);
  return {
    top: `${String(top * 100)}%`,
    height: `${String((bottom - top) * 100)}%`,
  };
}

function dataOf(p: Placed) {
  return {
    "data-col": p.col,
    "data-minute": p.minute,
    "data-minutes": p.minutes,
  };
}

function DayContents({
  day,
  col,
  timeZone,
  grid,
  children,
}: {
  day: WeekDayOut;
  col: number;
  timeZone: string;
  grid: boolean;
  children?: ReactNode;
}) {
  const headingId = useId();
  const free = openTime(day);
  const itemClass = grid
    ? "absolute inset-x-1 overflow-hidden rounded-sm px-1 text-xs"
    : "rounded-sm px-2 py-1 text-sm";
  return (
    <section
      aria-labelledby={headingId}
      className="flex min-w-0 flex-col gap-1"
    >
      <h3 id={headingId} className="text-sm font-semibold">
        {dayName(day.day)}
      </h3>
      {day.due.length > 0 && (
        <ul aria-label="Due" className="flex flex-col gap-1">
          {day.due.map((task) => (
            <li
              key={task.id}
              aria-label={`Due: ${task.title}`}
              className="rounded-sm bg-warning/15 px-1 text-xs"
            >
              Due: {task.title}
            </li>
          ))}
        </ul>
      )}
      {day.window === null ? (
        <p className="text-sm text-muted">No working hours</p>
      ) : (
        <div
          className={
            grid
              ? "relative h-[36rem] rounded-md border border-border bg-surface-muted"
              : "flex flex-col gap-1"
          }
        >
          <ul
            aria-label="Free time"
            className={grid ? "absolute inset-0" : "flex flex-col gap-1"}
          >
            {free.map((span) => (
              <li
                key={span.start}
                aria-label={`Free ${spanText(span, timeZone)}`}
                className={
                  grid
                    ? "absolute inset-x-0 bg-accent/10"
                    : "rounded-sm bg-accent/10 px-2 py-1 text-sm"
                }
                style={grid ? position(span, day) : undefined}
              >
                {grid ? null : `Free ${spanText(span, timeZone)}`}
              </li>
            ))}
          </ul>
          {children}
          {day.events.length > 0 && (
            <ul
              aria-label="Events"
              className={grid ? "contents" : "flex flex-col gap-1"}
            >
              {day.events.map((event, index) => {
                const label = `${event.title ?? "Busy"}, ${spanText(event, timeZone)}`;
                return (
                  <li
                    key={`${event.start}-${String(index)}`}
                    aria-label={label}
                    title={label}
                    {...dataOf(placed(event, col, timeZone))}
                    {...(event.busy ? { "data-busy": "" } : {})}
                    className={`${itemClass} ${event.matched ? "bg-accent/30" : "bg-muted/40"}`}
                    style={grid ? position(event, day) : undefined}
                  >
                    {event.title ?? "Busy"}
                  </li>
                );
              })}
            </ul>
          )}
          {day.planned.length > 0 && (
            <ul
              aria-label="Planned"
              className={grid ? "contents" : "flex flex-col gap-1"}
            >
              {day.planned.map((block) => {
                const label = `${block.title ?? "Planned for another project"}, ${spanText(block, timeZone)}`;
                return (
                  <li
                    key={`${block.start}-${block.task_id ?? ""}`}
                    aria-label={label}
                    title={label}
                    data-busy=""
                    {...dataOf(placed(block, col, timeZone))}
                    className={`${itemClass} border border-accent bg-surface`}
                    style={grid ? position(block, day) : undefined}
                  >
                    {block.title ?? "Planned"}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

// --- The laptop grid: drag, drop and keyboard ---------------------------------------------

function slotsOf(week: WeekOut, now: Date): Slot[] {
  return week.days.flatMap((day, col) =>
    openTime(day).flatMap((span) =>
      slotStarts(span, now).map((start) => ({
        day: day.day,
        col,
        start,
        minute: minuteOfDay(start, week.timezone),
      })),
    ),
  );
}

function SlotCell({
  slot,
  active,
  day,
}: {
  slot: Slot;
  active: boolean;
  day: WeekDayOut;
}) {
  const { setNodeRef, isOver } = useDroppable({
    id: `slot:${slot.start}`,
    data: slot,
  });
  return (
    <div
      ref={setNodeRef}
      aria-hidden="true"
      data-slot={slot.start}
      data-col={slot.col}
      data-minute={slot.minute}
      data-minutes={15}
      className={`absolute inset-x-0 ${isOver || active ? "bg-accent/40" : ""}`}
      style={position(blockFrom(slot.start, 15), day)}
    />
  );
}

/** The slot at the same time (or the next free one, else the day's last) on the nearest
 * day after (`step` 1) or before (`step` -1) the current slot's day that has free slots;
 * the current slot when there is none. */
function acrossDays(
  slots: readonly Slot[],
  index: number,
  step: 1 | -1,
): number {
  const current = slots[index];
  if (!current) return index;
  for (let col = current.col + step; col >= 0 && col < 7; col += step) {
    const day = slots.flatMap((slot, i) =>
      slot.col === col ? [{ slot, i }] : [],
    );
    const found =
      day.find(({ slot }) => slot.minute >= current.minute) ?? day.at(-1);
    if (found) return found.i;
  }
  return index;
}

/** Where the pointer ended a drag: the activator's point plus the drag's delta. */
function endPoint(event: DragEndEvent): { x: number; y: number } | null {
  const e = event.activatorEvent;
  let x: number;
  let y: number;
  if (typeof TouchEvent !== "undefined" && e instanceof TouchEvent) {
    const touch = e.touches[0] ?? e.changedTouches[0];
    if (!touch) return null;
    ({ clientX: x, clientY: y } = touch);
  } else if (e instanceof MouseEvent) {
    ({ clientX: x, clientY: y } = e);
  } else {
    return null;
  }
  return { x: x + event.delta.x, y: y + event.delta.y };
}

function WeekGrid({
  week,
  now,
  say,
  onDrop,
  onSchedule,
}: {
  week: WeekOut;
  now: Date;
  say: (text: string) => void;
  onDrop: (task: TaskRefOut, day: string, start: string) => void;
  onSchedule: (task: TaskRefOut) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const slots = useMemo(() => slotsOf(week, now), [week, now]);
  const [picked, setPicked] = useState<{
    task: TaskRefOut;
    index: number;
  } | null>(null);
  const sensors = useSensors(
    useSensor(MouseSensor),
    useSensor(TouchSensor, {
      activationConstraint: { delay: 250, tolerance: 5 },
    }),
  );
  const where = (slot: Slot) =>
    `${dayName(slot.day)}, ${clockTime(slot.start, week.timezone)}`;

  const onDragEnd = (event: DragEndEvent) => {
    const task = week.unscheduled.find((t) => t.id === event.active.id);
    if (!task) return;
    const slot = event.over?.data.current as Slot | undefined;
    if (slot) {
      onDrop(task, slot.day, slot.start);
      return;
    }
    // Busy time has no drop target (dnd-kit reports no `over`): say so when the card
    // was let go over it.
    const point = endPoint(event);
    const busy =
      root.current?.querySelectorAll<HTMLElement>("[data-busy]") ?? [];
    if (
      point &&
      [...busy].some((el) => {
        const r = el.getBoundingClientRect();
        return (
          point.x >= r.left &&
          point.x <= r.right &&
          point.y >= r.top &&
          point.y <= r.bottom
        );
      })
    ) {
      say(BUSY);
    }
  };

  const onKey = (task: TaskRefOut, event: KeyboardEvent<HTMLButtonElement>) => {
    const key = event.key;
    if (!picked) {
      if (key !== " " && key !== "Enter") return;
      event.preventDefault();
      if (slots.length === 0) {
        say("No free time this week");
        return;
      }
      setPicked({ task, index: 0 });
      const first = slots[0];
      if (first) {
        say(
          `Picked up ${task.title}. ${where(first)}. Arrow keys choose a time, Space drops, Escape cancels.`,
        );
      }
      return;
    }
    const current = slots[picked.index];
    if (!current) return;
    let next: number;
    if (key === "ArrowDown")
      next = Math.min(picked.index + 1, slots.length - 1);
    else if (key === "ArrowUp") next = Math.max(picked.index - 1, 0);
    else if (key === "ArrowRight" || key === "ArrowLeft") {
      const step = key === "ArrowRight" ? 1 : -1;
      next = acrossDays(slots, picked.index, step);
    } else if (key === " " || key === "Enter") {
      event.preventDefault();
      setPicked(null);
      onDrop(picked.task, current.day, current.start);
      return;
    } else if (key === "Escape") {
      event.preventDefault();
      setPicked(null);
      say(`${picked.task.title} was not scheduled`);
      return;
    } else {
      return;
    }
    event.preventDefault();
    setPicked({ ...picked, index: next });
    const slot = slots[next];
    if (slot) say(where(slot));
  };

  const active = picked ? slots[picked.index]?.start : undefined;
  return (
    <DndContext
      sensors={sensors}
      collisionDetection={pointerWithin}
      onDragEnd={onDragEnd}
    >
      <div ref={root} className="flex flex-col gap-4">
        <div className="grid grid-cols-7 gap-2">
          {week.days.map((day, col) => (
            <DayContents
              key={day.day}
              day={day}
              col={col}
              timeZone={week.timezone}
              grid
            >
              {slots
                .filter((s) => s.col === col)
                .map((slot) => (
                  <SlotCell
                    key={slot.start}
                    slot={slot}
                    day={day}
                    active={slot.start === active}
                  />
                ))}
            </DayContents>
          ))}
        </div>
        <ToSchedule
          tasks={week.unscheduled}
          onSchedule={onSchedule}
          card={(task) => (
            <DraggableCard
              task={task}
              picked={picked?.task.id === task.id}
              onKeyDown={(event) => {
                onKey(task, event);
              }}
              onBlur={() => {
                if (picked?.task.id === task.id) setPicked(null);
              }}
            />
          )}
        />
      </div>
    </DndContext>
  );
}

function cardName(task: TaskRefOut): string {
  return `${task.title}, ${String(task.estimate_minutes ?? 0)} minutes`;
}

function DraggableCard({
  task,
  picked,
  onKeyDown,
  onBlur,
}: {
  task: TaskRefOut;
  picked: boolean;
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
  onBlur: () => void;
}) {
  const { setNodeRef, listeners, transform, isDragging } = useDraggable({
    id: task.id,
  });
  return (
    <button
      ref={setNodeRef}
      type="button"
      data-card=""
      aria-label={cardName(task)}
      aria-pressed={picked}
      aria-describedby={undefined}
      {...listeners}
      onKeyDown={onKeyDown}
      onBlur={onBlur}
      style={{ transform: CSS.Translate.toString(transform) }}
      className={`min-h-11 flex-1 touch-none rounded-md border px-3 py-2 text-left text-sm ${
        picked || isDragging
          ? "border-accent bg-accent/10"
          : "border-border bg-surface"
      }`}
    >
      {task.title} · {String(task.estimate_minutes ?? 0)} min
    </button>
  );
}

// --- The phone's day list ------------------------------------------------------------------

function DayList({
  week,
  onSchedule,
}: {
  week: WeekOut;
  onSchedule: (task: TaskRefOut) => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <ToSchedule tasks={week.unscheduled} onSchedule={onSchedule} />
      {week.days.map((day, col) => (
        <DayContents
          key={day.day}
          day={day}
          col={col}
          timeZone={week.timezone}
          grid={false}
        />
      ))}
    </div>
  );
}

function ToSchedule({
  tasks,
  onSchedule,
  card,
}: {
  tasks: TaskRefOut[];
  onSchedule: (task: TaskRefOut) => void;
  card?: (task: TaskRefOut) => ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      {tasks.length === 0 ? (
        <p className="text-sm text-muted">
          Nothing left to schedule this week.
        </p>
      ) : (
        <ul aria-label="To schedule" className="flex flex-col gap-2">
          {tasks.map((task) => (
            <li key={task.id} className="flex items-center gap-2">
              {card ? (
                card(task)
              ) : (
                <span className="flex-1 text-sm">
                  {task.title} · {String(task.estimate_minutes ?? 0)} min
                </span>
              )}
              <button
                type="button"
                className="min-h-11 rounded-md border border-border px-3 text-sm"
                onClick={() => {
                  onSchedule(task);
                }}
              >
                Schedule
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --- The slot picker ---------------------------------------------------------------------

function SlotPicker({
  task,
  week,
  now,
  onPick,
  onClose,
}: {
  task: TaskRefOut;
  week: WeekOut;
  now: Date;
  onPick: (day: string, start: string) => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    close.current?.focus();
  }, []);
  const minutes = task.estimate_minutes ?? 0;
  const days = week.days
    .map((day) => ({ day, starts: fittingStarts(openTime(day), minutes, now) }))
    .filter(({ starts }) => starts.length > 0);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex max-h-[80vh] w-full max-w-md flex-col gap-3 overflow-y-auto rounded-lg bg-surface p-4 shadow-lg"
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
      >
        <h2 id={titleId} className="text-lg font-semibold">
          Schedule {task.title}
        </h2>
        {days.length === 0 && (
          <p className="text-sm text-muted">
            No free time this week fits {String(minutes)} minutes.
          </p>
        )}
        {days.map(({ day, starts }) => (
          <div
            key={day.day}
            role="group"
            aria-label={dayName(day.day)}
            className="flex flex-col gap-1"
          >
            <p className="text-sm font-semibold">{dayName(day.day)}</p>
            <div className="flex flex-wrap gap-1">
              {starts.map((start) => (
                <button
                  key={start}
                  type="button"
                  className="min-h-11 rounded-md border border-border px-2 text-sm"
                  onClick={() => {
                    onPick(day.day, start);
                  }}
                >
                  {spanText(blockFrom(start, minutes), week.timezone)}
                </button>
              ))}
            </div>
          </div>
        ))}
        <button
          ref={close}
          type="button"
          className="min-h-11 self-end rounded-md border border-border px-3 text-sm"
          onClick={onClose}
        >
          Close
        </button>
      </div>
    </div>
  );
}

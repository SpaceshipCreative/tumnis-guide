// The dashboard's calendar strip (P1-10, FR-1.3): today's working window in the workspace
// timezone as one bar, the free blocks highlighted on it, the events from every account
// laid over them and a marker at the current time. Every segment carries its times and
// length as its accessible name (times in the zone the server computed the window in), so
// the bar reads the same on a phone, where it is too narrow for text, as on a laptop.
import { useQuery } from "@tanstack/react-query";
import { useId } from "react";

import { planningGetDayCalendarOptions } from "../../api/@tanstack/react-query.gen";
import type { DayCalendarOut } from "../../api/types.gen";
import { formatMinutes } from "./format";

/** `GET /v1/plan/{day}/calendar`: the day's window, events and free blocks. */
export const dayCalendarQuery = (day: string) =>
  planningGetDayCalendarOptions({ path: { day } });

export interface CalendarStripProps {
  /** The local day to show, `YYYY-MM-DD` in the workspace timezone. */
  day: string;
}

export function CalendarStrip({ day }: CalendarStripProps) {
  const headingId = useId();
  const calendar = useQuery(dayCalendarQuery(day));
  // Until the first answer, a placeholder of the strip's height holds the layout.
  if (calendar.isPending) return <div aria-hidden="true" className="h-24" />;
  return (
    <section
      aria-labelledby={headingId}
      className="flex shrink-0 flex-col gap-2"
    >
      <h2 id={headingId} className="text-lg font-semibold">
        Calendar and free blocks
      </h2>
      {calendar.data ? (
        <Strip calendar={calendar.data} now={new Date()} />
      ) : (
        <p className="text-sm text-muted">The calendar could not be loaded.</p>
      )}
    </section>
  );
}

interface Span {
  start: string;
  end: string;
}

/** "HH:MM" in `timeZone` (24-hour, the working hours' own notation). */
function clockTime(at: Date, timeZone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  }).format(at);
}

function percent(value: number): string {
  return `${String(Number(value.toFixed(4)))}%`;
}

/** Where a span sits on the window's bar, clipped to it: `left` and `width` in %. */
function placement(span: Span, range: Span): { left: string; width: string } {
  const from = Date.parse(range.start);
  const length = Date.parse(range.end) - from;
  const clamp = (at: number) => Math.min(Math.max(at, 0), 1);
  const left = clamp((Date.parse(span.start) - from) / length);
  const right = clamp((Date.parse(span.end) - from) / length);
  return { left: percent(left * 100), width: percent((right - left) * 100) };
}

function Strip({ calendar, now }: { calendar: DayCalendarOut; now: Date }) {
  const { window: workWindow, timezone } = calendar;
  const at = (iso: string) => clockTime(new Date(iso), timezone);
  const range = (span: Span) => `${at(span.start)} to ${at(span.end)}`;
  const freeMinutes = calendar.free_blocks.reduce(
    (sum, b) => sum + b.minutes,
    0,
  );
  const nowAt =
    workWindow !== null &&
    now.getTime() >= Date.parse(workWindow.start) &&
    now.getTime() <= Date.parse(workWindow.end)
      ? placement(
          { start: now.toISOString(), end: now.toISOString() },
          workWindow,
        ).left
      : null;

  const freeItems = calendar.free_blocks.map((block) => (
    <li
      key={block.start}
      aria-label={`Free ${range(block)}, ${String(block.minutes)} minutes`}
      title={`Free ${range(block)}`}
      data-kind="free"
      className="absolute inset-y-0 rounded-sm bg-accent/25 ring-1 ring-accent ring-inset"
      style={workWindow === null ? undefined : placement(block, workWindow)}
    />
  ));
  const eventItems = calendar.events.map((event, index) => {
    const title = event.title ?? "Untitled event";
    const label = event.busy
      ? `Busy: ${title}, ${range(event)}, ${event.account}`
      : `Not blocking: ${title}, ${range(event)}, ${event.account}`;
    return (
      <li
        key={`${event.start}-${String(index)}`}
        aria-label={label}
        title={label}
        data-kind={event.busy ? "busy" : "tentative"}
        className={
          event.busy
            ? "absolute inset-y-1 rounded-sm bg-muted/70"
            : "absolute inset-y-3 rounded-sm border border-dashed border-muted"
        }
        style={workWindow === null ? undefined : placement(event, workWindow)}
      />
    );
  });

  return (
    <div className="flex flex-col gap-1">
      {workWindow === null ? (
        <p className="text-sm text-muted">No working hours today.</p>
      ) : (
        <p className="text-sm text-muted">
          {range(workWindow)} · {formatMinutes(freeMinutes)} free
        </p>
      )}
      <div
        className={
          workWindow === null
            ? "sr-only"
            : "relative h-10 overflow-hidden rounded-md border border-border bg-surface-muted"
        }
      >
        <ul aria-label="Free blocks" className="absolute inset-0">
          {freeItems}
        </ul>
        <ul aria-label="Events" className="absolute inset-0">
          {eventItems}
        </ul>
        {nowAt !== null && (
          <div
            role="img"
            aria-label={`Now ${clockTime(now, timezone)}`}
            className="absolute inset-y-0 w-0.5 bg-danger"
            style={{ left: nowAt }}
          />
        )}
      </div>
      {workWindow !== null && (
        <div
          className="flex justify-between text-xs text-muted"
          aria-hidden="true"
        >
          <span>{at(workWindow.start)}</span>
          <span>{at(workWindow.end)}</span>
        </div>
      )}
    </div>
  );
}

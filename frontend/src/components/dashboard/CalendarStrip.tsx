// The dashboard's calendar strip (P1-10, FR-1.3): interface stub until the implementation.
export interface CalendarStripProps {
  /** The local day to show, `YYYY-MM-DD` in the workspace timezone. */
  day: string;
}

export function CalendarStrip(props: CalendarStripProps) {
  return props.day === "" ? null : null;
}

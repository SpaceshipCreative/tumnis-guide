// The project's Calendar view (P1-12, FR-2.6): interface stub until the implementation.
export interface CalendarViewProps {
  projectId: string;
  /** The ISO Monday of the week to show; undefined: the current week. */
  week: string | undefined;
  onWeek: (monday: string) => void;
}

export function CalendarView(props: CalendarViewProps) {
  return props.projectId === "" ? null : null;
}

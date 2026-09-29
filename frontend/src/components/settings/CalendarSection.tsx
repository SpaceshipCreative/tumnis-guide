// Settings > Calendar (P1-09): interface stub until the implementation.
export interface CalendarSectionProps {
  /** Opens Google's consent page (the whole window by default). */
  openUrl?: (url: string) => void;
}

export function CalendarSection(props: CalendarSectionProps) {
  return props.openUrl === undefined ? null : null;
}

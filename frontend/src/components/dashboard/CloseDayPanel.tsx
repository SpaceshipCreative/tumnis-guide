// Close the day (P1-18, J7): stub until the panel lands.
export function CloseDayPanel({
  day,
}: {
  day: string;
  onClose: () => void;
}): React.JSX.Element | null {
  return day ? null : null;
}

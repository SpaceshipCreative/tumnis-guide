// A value that follows another after it has been still for `ms` (P0-25: the typeaheads
// ask the server once per pause in typing, not once per keystroke).
import { useEffect, useState } from "react";

export const TYPEAHEAD_DEBOUNCE_MS = 150; // (plan default)

export function useDebounced<T>(value: T, ms = TYPEAHEAD_DEBOUNCE_MS): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSettled(value);
    }, ms);
    return () => {
      window.clearTimeout(timer);
    };
  }, [value, ms]);
  return settled;
}

// Layout branches in script (P0-24): laptop from 768 px, like Tailwind's `md`. Without
// `matchMedia` (a bare test environment) the laptop layout renders.
import { useSyncExternalStore } from "react";

export const LAPTOP_QUERY = "(min-width: 768px)";

function mediaList(query: string): MediaQueryList | null {
  return typeof window.matchMedia === "function"
    ? window.matchMedia(query)
    : null;
}

export function useMediaQuery(query: string, fallback = true): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const list = mediaList(query);
      list?.addEventListener("change", onChange);
      return () => list?.removeEventListener("change", onChange);
    },
    () => mediaList(query)?.matches ?? fallback,
    () => fallback,
  );
}

export function useIsLaptop(): boolean {
  return useMediaQuery(LAPTOP_QUERY);
}

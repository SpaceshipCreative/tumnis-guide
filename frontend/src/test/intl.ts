// `mockIntlZone(zone)`: this device's time zone as `Intl.DateTimeFormat` reports it (P0-26).
// Vitest's `restoreMocks` puts the real one back after each test.
import { vi } from "vitest";

export function mockIntlZone(zone: string): void {
  const real = new Intl.DateTimeFormat().resolvedOptions();
  vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
    ...real,
    timeZone: zone,
  });
}

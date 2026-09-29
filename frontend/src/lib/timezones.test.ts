import { expect, test, vi } from "vitest";

import { mockIntlZone } from "../test/intl";
import { browserZone, ianaZones } from "./timezones";

test(
  "[P0-26][REL-6] T-P0-26-16 ianaZones always holds UTC, with or without Intl.supportedValuesOf",
  () => {
    const zones = ianaZones();
    expect(zones).toContain("UTC");
    expect(zones).toContain("America/New_York");
    expect(new Set(zones).size).toBe(zones.length);

    vi.stubGlobal("Intl", { ...Intl, supportedValuesOf: undefined });
    const fallback = ianaZones();
    expect(fallback).toContain("UTC");
    expect(fallback).toContain("Australia/Sydney");
    vi.unstubAllGlobals();

    mockIntlZone("Pacific/Auckland");
    expect(browserZone()).toBe("Pacific/Auckland");
  },
);

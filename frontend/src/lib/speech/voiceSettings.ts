// Settings > Voice (P4-03, FR-10.8, FR-11.7): the workspace setting `voice`, read through the
// generic settings section route. Settings > Voice and the focus bar's voice mode read the
// same query, so a save there turns voice mode on or off here at once.
import { settingsGetSectionOptions } from "../../api/@tanstack/react-query.gen";
import type { SettingSectionOut } from "../../api/types.gen";
import type { Level } from "../levelRules";

export const VOICE_SECTION = "voice";
export const VOICE_LEVELS: readonly Level[] = [
  "nudge",
  "coach",
  "guardrail",
] as const;

export interface Voice {
  enabledLevels: Level[];
  engine: "browser" | "server";
  serverProvider: "piper" | "hosted";
  hostedAllowed: boolean;
}

export const voiceQuery = () =>
  settingsGetSectionOptions({ path: { section: VOICE_SECTION } });

function isLevel(value: unknown): value is Level {
  return (
    value === "quiet" ||
    value === "nudge" ||
    value === "coach" ||
    value === "guardrail"
  );
}

/** The stored values with the server's defaults filled in (voice off, browser engine). */
export function readVoice(section: SettingSectionOut | undefined): Voice {
  const v = section?.values ?? {};
  const levels = Array.isArray(v.enabled_levels) ? v.enabled_levels : [];
  return {
    enabledLevels: levels.filter(isLevel),
    engine: v.engine === "server" ? "server" : "browser",
    serverProvider: v.server_provider === "hosted" ? "hosted" : "piper",
    hostedAllowed: v.hosted_allowed === true,
  };
}

/** The values a PUT sends (the server's field names). */
export function voiceValues(voice: Voice): Record<string, unknown> {
  return {
    enabled_levels: voice.enabledLevels,
    engine: voice.engine,
    server_provider: voice.serverProvider,
    hosted_allowed: voice.hostedAllowed,
  };
}

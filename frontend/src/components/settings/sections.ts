// The Settings sections (P0-22's route, P0-26's screens): URL segment and label.
export const SETTINGS_SECTIONS = [
  "account",
  "sessions",
  "keys",
  "audit",
  "dead-letters",
  "workspace",
  "storage",
] as const;
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];

export const SECTION_LABELS: Record<SettingsSection, string> = {
  account: "Account",
  sessions: "Sessions",
  keys: "API keys",
  audit: "Audit log",
  "dead-letters": "Dead letters",
  workspace: "Workspace",
  storage: "Storage",
};

export function isSettingsSection(value: string): value is SettingsSection {
  return (SETTINGS_SECTIONS as readonly string[]).includes(value);
}

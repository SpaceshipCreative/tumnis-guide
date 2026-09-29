// The Settings sections, in order (P0-26): a side list on a laptop, the section list
// behind "All settings" on a phone.
import { Link } from "@tanstack/react-router";

import type { SettingsSection } from "./sections";
import { SECTION_LABELS, SETTINGS_SECTIONS } from "./sections";

export function SettingsNav({
  onPick,
}: {
  onPick?: (section: SettingsSection) => void;
}) {
  return (
    <nav aria-label="Settings sections">
      <ul className="flex flex-col gap-1">
        {SETTINGS_SECTIONS.map((section) => (
          <li key={section}>
            <Link
              to="/settings/$section"
              params={{ section }}
              className="flex min-h-11 items-center rounded-md px-3 py-2 text-muted hover:bg-surface-muted"
              activeProps={{
                className: "bg-surface-muted font-medium text-text",
                "aria-current": "page",
              }}
              onClick={() => onPick?.(section)}
            >
              {SECTION_LABELS[section]}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

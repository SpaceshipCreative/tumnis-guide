// Tasks | Board (P0-24, FR-2.6, FR-2.8): tabs on a laptop, a segmented control (radio
// buttons) on the phone.
import { useId } from "react";

import type { ProjectView } from "../../lib/views";

const VIEW_LABELS: Record<ProjectView, string> = {
  tasks: "Tasks",
  board: "Board",
  calendar: "Calendar",
};
const VIEWS = Object.keys(VIEW_LABELS) as ProjectView[];

/** The id of a view's tab; the page's tabpanel names it in `aria-labelledby`. */
export function viewTabId(panelId: string, view: ProjectView): string {
  return `${panelId}-tab-${view}`;
}

export function ViewSwitcher({
  view,
  laptop,
  panelId,
  onChange,
}: {
  view: ProjectView;
  laptop: boolean;
  /** The id of the tabpanel holding the view (laptop). */
  panelId: string;
  onChange: (view: ProjectView) => void;
}) {
  const name = useId();
  if (laptop) {
    return (
      <div
        role="tablist"
        aria-label="Views"
        className="flex gap-1 border-b border-border"
        onKeyDown={(event) => {
          if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
          event.preventDefault();
          const step = event.key === "ArrowRight" ? 1 : -1;
          const next =
            VIEWS[(VIEWS.indexOf(view) + step + VIEWS.length) % VIEWS.length];
          if (!next) return;
          onChange(next);
          document.getElementById(viewTabId(panelId, next))?.focus();
        }}
      >
        {VIEWS.map((v) => (
          <button
            key={v}
            id={viewTabId(panelId, v)}
            type="button"
            role="tab"
            aria-selected={v === view}
            aria-controls={panelId}
            tabIndex={v === view ? 0 : -1}
            onClick={() => {
              onChange(v);
            }}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
              v === view
                ? "border-accent text-text"
                : "border-transparent text-muted hover:text-text"
            }`}
          >
            {VIEW_LABELS[v]}
          </button>
        ))}
      </div>
    );
  }
  return (
    <div
      role="radiogroup"
      aria-label="Views"
      className="grid grid-cols-3 rounded-md border border-border bg-surface-muted p-1"
    >
      {VIEWS.map((v) => (
        <label
          key={v}
          className={`relative flex min-h-11 cursor-pointer items-center justify-center rounded text-sm font-medium has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent ${
            v === view ? "bg-surface text-text shadow-sm" : "text-muted"
          }`}
        >
          <input
            type="radio"
            name={name}
            value={v}
            checked={v === view}
            onChange={() => {
              onChange(v);
            }}
            // A transparent input over the whole segment: a tap or click lands on the
            // radio itself.
            className="absolute inset-0 m-0 cursor-pointer appearance-none opacity-0"
          />
          {VIEW_LABELS[v]}
        </label>
      ))}
    </div>
  );
}

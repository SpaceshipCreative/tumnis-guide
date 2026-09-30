// The Context rail (P0-24, FR-2.2, FR-2.7): laptop only, beside the centre column.
import type { Project } from "../types";
import { RailSections } from "./RailSections";

export function RightRail({
  project,
  onOpenTask,
}: {
  project: Project;
  onOpenTask: (taskId: string) => void;
}) {
  return (
    <aside
      aria-label="Context"
      className="sticky top-[calc(var(--tg-header-h)+1rem)] flex max-h-[calc(100dvh-var(--tg-header-h)-2rem)] w-72 shrink-0 flex-col gap-2 self-start overflow-y-auto rounded-xl border border-border bg-surface px-4 py-3 shadow-card"
    >
      <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">
        Context
      </h2>
      <RailSections project={project} onOpenTask={onOpenTask} />
    </aside>
  );
}

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
      className="sticky top-4 flex max-h-[calc(100dvh-2rem)] w-72 shrink-0 flex-col gap-2 self-start overflow-y-auto rounded-lg border border-border bg-surface px-4 py-3"
    >
      <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">
        Context
      </h2>
      <RailSections project={project} onOpenTask={onOpenTask} />
    </aside>
  );
}

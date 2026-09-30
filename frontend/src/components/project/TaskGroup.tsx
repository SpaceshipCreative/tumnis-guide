// One group of the Tasks view (P0-24, FR-2.6): a region named after the group; a subtask
// sits under its parent when the parent is in the same group.
import { useId } from "react";

import type { TaskLite } from "./grouping";
import { TaskRow } from "./TaskRow";

export function TaskGroup({
  label,
  tasks,
  onOpen,
}: {
  label: string;
  tasks: readonly TaskLite[];
  onOpen: (taskId: string) => void;
}) {
  const headingId = useId();
  const here = new Set(tasks.map((t) => t.id));
  const top = tasks.filter(
    (t) => t.parent_id === null || !here.has(t.parent_id),
  );
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2">
      <h2
        id={headingId}
        className="text-sm font-semibold tracking-wide text-muted uppercase"
      >
        {label}
      </h2>
      {tasks.length === 0 ? (
        <p className="text-sm text-muted">Nothing here</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {top.map((task) => (
            <TaskRow
              key={task.id}
              task={task}
              subtasksOf={(id) => tasks.filter((t) => t.parent_id === id)}
              onOpen={onOpen}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

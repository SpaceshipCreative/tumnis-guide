// One group of the Tasks view (P0-24, FR-2.6): a region named after the group; a subtask
// sits under its parent when the parent is in the same group. A card (DS-01): the group's
// name in the header row, its tasks as rows divided by the border colour.
import { Card } from "../common/Card";
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
  const here = new Set(tasks.map((t) => t.id));
  const top = tasks.filter(
    (t) => t.parent_id === null || !here.has(t.parent_id),
  );
  return (
    <Card
      title={label}
      bodyClassName={tasks.length === 0 ? "px-4 py-3" : "px-4 py-1"}
    >
      {tasks.length === 0 ? (
        <p className="text-sm text-muted">Nothing here</p>
      ) : (
        <ul className="flex flex-col divide-y divide-border">
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
    </Card>
  );
}

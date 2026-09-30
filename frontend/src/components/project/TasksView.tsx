// The Tasks view (P0-24, FR-2.6, UX 7): five groups, each a region; `j`/`k` move between
// rows, Enter opens the focused one, `s` starts and `d` marks done.
import type { KeyboardEvent } from "react";

import { GROUP_LABELS, GROUPS, groupTasks, type TaskLite } from "./grouping";
import { TaskGroup } from "./TaskGroup";

function onShortcut(event: KeyboardEvent<HTMLElement>): void {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  const target = event.target as HTMLElement;
  if (target.closest("input, textarea, select")) return;
  const opens = [
    ...event.currentTarget.querySelectorAll<HTMLElement>("[data-task-open]"),
  ];
  const row = target.closest("li[data-task-id]");
  const current = row?.querySelector<HTMLElement>("[data-task-open]") ?? null;
  const at = current ? opens.indexOf(current) : -1;
  if (event.key === "j" || event.key === "k") {
    const next = opens[event.key === "j" ? at + 1 : Math.max(at - 1, 0)];
    next?.focus();
    event.preventDefault();
  } else if ((event.key === "s" || event.key === "d") && row) {
    // The row's own action: a subtask row nested inside it has its own.
    [
      ...row.querySelectorAll<HTMLElement>(
        `[data-status-action="${event.key}"]`,
      ),
    ]
      .find((button) => button.closest("li[data-task-id]") === row)
      ?.click();
  }
}

export function TasksView({
  tasks,
  now,
  timezone,
  onOpen,
}: {
  tasks: readonly TaskLite[];
  now: Date;
  timezone: string;
  onOpen: (taskId: string) => void;
}) {
  const groups = groupTasks(tasks, now, timezone);
  return (
    <div className="flex flex-col gap-6" onKeyDown={onShortcut}>
      {GROUPS.map((group) => (
        <TaskGroup
          key={group}
          label={GROUP_LABELS[group]}
          tasks={groups[group]}
          onOpen={onOpen}
        />
      ))}
    </div>
  );
}

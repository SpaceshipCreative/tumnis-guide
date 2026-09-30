// A card's checklist (P0-24, FR-3.4, FR-3.8): its subtasks below the card threshold, as
// the server's board layout nests them (the board and the rule never disagree).
import type { Board } from "../project/types";

type Item = Board["columns"][number]["cards"][number]["checklist"][number];

export function Checklist({ items }: { items: readonly Item[] }) {
  const done = items.filter((t) => t.status === "done").length;
  return (
    <div className="flex flex-col gap-1 border-t border-border pt-2">
      <p className="text-xs text-muted">
        Checklist {done}/{items.length}
      </p>
      <ul aria-label="Checklist" className="flex flex-col gap-0.5 text-xs">
        {items.map((item) => (
          <li key={item.id} className="flex items-baseline gap-1.5">
            <span aria-hidden="true">{item.status === "done" ? "☑" : "☐"}</span>
            <span
              className={
                item.status === "done" ? "text-muted line-through" : ""
              }
            >
              {item.title}
            </span>
            <span className="sr-only">
              {item.status === "done" ? "(done)" : "(open)"}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

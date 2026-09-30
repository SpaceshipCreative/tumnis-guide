// The composer (P0-24, FR-2.5): one input; Enter creates a task in this project (label
// pending, Backlog). The write goes through the offline queue like every capture
// (P0-25): the row shows under Up next at once with its pending mark, online or not.
import { useState } from "react";

import { useEnqueueTask } from "../quickadd/queue";

export function Composer({ projectId }: { projectId: string }) {
  const [title, setTitle] = useState("");
  const enqueue = useEnqueueTask();
  return (
    <form
      className="flex"
      onSubmit={(event) => {
        event.preventDefault();
        const trimmed = title.trim();
        if (trimmed === "") return;
        enqueue({ project_id: projectId, title: trimmed });
        setTitle("");
      }}
    >
      <input
        type="text"
        aria-label="New task"
        placeholder="Add a task and press Enter"
        value={title}
        maxLength={500}
        onChange={(event) => {
          setTitle(event.target.value);
        }}
        className="min-h-11 w-full rounded-md border border-border bg-surface px-3 text-base md:min-h-9 md:text-sm"
      />
    </form>
  );
}

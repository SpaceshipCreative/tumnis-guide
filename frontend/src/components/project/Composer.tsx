// The composer (P0-24, FR-2.5): one input; Enter creates a task in this project (label
// pending, Backlog). The row shows under Up next at once (P0-25 moves the write onto the
// offline queue).
import { useState } from "react";

import { useCreateTask } from "./mutations";

export function Composer({ projectId }: { projectId: string }) {
  const [title, setTitle] = useState("");
  const create = useCreateTask();
  return (
    <form
      className="flex"
      onSubmit={(event) => {
        event.preventDefault();
        const trimmed = title.trim();
        if (trimmed === "") return;
        create.mutate({ projectId, title: trimmed });
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

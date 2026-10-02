// The composer (P0-24, FR-2.5): one input; Enter creates a task in this project (label
// pending, Backlog). The write goes through the offline queue like every capture
// (P0-25): the row shows under Up next at once with its pending mark, online or not.
// Ask the agent (P2-17): the toggle turns the same input into a question, sent to
// POST /v1/projects/{id}/ask, which makes an AI task and starts its run; the answer
// lands as the task's result. The composer goes back to task mode after sending.
// Files (FR-15.2): a file dropped on the composer is uploaded to the project's knowledge
// (scanned, then extracted); once the server accepts it, `onFileAdded` shows where it went.
import { useQueryClient } from "@tanstack/react-query";
import { type DragEvent, useState } from "react";

import type { AskOut } from "../../api/types.gen";
import { zAskOut } from "../../api/zod.gen";
import { ApiError, apiWrite, useWrite } from "../../lib/fetch";
import { invalidateTaskViews } from "../../lib/task-cache";
import { uiStore } from "../../stores/uiStore";
import { BUTTON_SECONDARY } from "../common/ui";
import { useEnqueueTask } from "../quickadd/queue";
import { useKnowledgeUpload } from "./rail/useKnowledgeUpload";

function carriesFiles(event: DragEvent): boolean {
  return Array.from(event.dataTransfer.types).includes("Files");
}

function askRefusal(error: unknown): string {
  if (error instanceof ApiError && error.problem.code === "no_ready_profile") {
    return "This project has no ready agent yet.";
  }
  return "Could not ask the agent. Try again.";
}

export function Composer({
  projectId,
  onFileAdded,
}: {
  projectId: string;
  onFileAdded?: () => void;
}) {
  const [title, setTitle] = useState("");
  const [asking, setAsking] = useState(false);
  const [dropping, setDropping] = useState(false);
  const upload = useKnowledgeUpload(projectId);
  const dropFiles = (files: File[]) => {
    for (const file of files) {
      upload(file).then(
        () => onFileAdded?.(),
        () => {
          uiStore.trigger.showNotice({
            text: `Could not upload ${file.name}.`,
          });
        },
      );
    }
  };
  const enqueue = useEnqueueTask();
  const queryClient = useQueryClient();
  const ask = useWrite<{ question: string; idempotencyKey?: string }, AskOut>({
    mutationFn: ({ question, idempotencyKey }) =>
      apiWrite<AskOut>({
        kind: "create",
        method: "POST",
        path: `/projects/${encodeURIComponent(projectId)}/ask`,
        body: { question },
        idempotencyKey,
        schema: zAskOut,
      }),
    onSuccess: () => {
      setTitle("");
      setAsking(false);
    },
    onError: (error) => {
      uiStore.trigger.showNotice({ text: askRefusal(error) });
    },
    onSettled: () => invalidateTaskViews(queryClient),
  });
  const label = asking ? "Ask the agent" : "New task";
  return (
    <form
      aria-label="Composer"
      className={`flex gap-2 rounded-md ${
        dropping ? "outline-2 outline-offset-2 outline-accent" : ""
      }`}
      onDragEnter={(event) => {
        if (!carriesFiles(event)) return;
        event.preventDefault();
        setDropping(true);
      }}
      onDragOver={(event) => {
        if (!carriesFiles(event)) return;
        event.preventDefault(); // allows the drop
        event.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={(event) => {
        if (event.currentTarget.contains(event.relatedTarget as Node | null)) {
          return;
        }
        setDropping(false);
      }}
      onDrop={(event) => {
        if (!carriesFiles(event)) return;
        event.preventDefault();
        setDropping(false);
        dropFiles(Array.from(event.dataTransfer.files));
      }}
      onSubmit={(event) => {
        event.preventDefault();
        const trimmed = title.trim();
        if (trimmed === "") return;
        if (asking) {
          if (!ask.isPending) ask.mutate({ question: trimmed });
          return;
        }
        enqueue({ project_id: projectId, title: trimmed });
        setTitle("");
      }}
    >
      <input
        type="text"
        aria-label={label}
        placeholder={
          asking
            ? "Ask a question and press Enter"
            : "Add a task and press Enter"
        }
        value={title}
        maxLength={asking ? 4000 : 500}
        onChange={(event) => {
          setTitle(event.target.value);
        }}
        className="min-h-11 w-full rounded-md border border-border bg-surface px-3 text-base md:min-h-9 md:text-sm"
      />
      <button
        type="button"
        aria-pressed={asking}
        onClick={() => {
          setAsking((a) => !a);
        }}
        className={`${BUTTON_SECONDARY} shrink-0 px-3 md:min-h-9 ${
          asking ? "border-accent text-accent" : ""
        }`}
      >
        Ask the agent
      </button>
    </form>
  );
}

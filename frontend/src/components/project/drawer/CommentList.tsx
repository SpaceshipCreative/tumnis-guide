// A task's comments (P0-24): oldest first, and a field to add one. APP-F04: the button
// stays focusable while a comment saves (aria-disabled, not disabled: a disabled control
// drops focus to <body>, outside the drawer), and focus goes back to the field once the
// comment is added, ready for the next one.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";

import { zCommentOut } from "../../../api/zod.gen";
import { apiWrite, useWrite } from "../../../lib/fetch";
import { invalidateTaskViews } from "../../../lib/task-cache";
import { uiStore } from "../../../stores/uiStore";
import { commentsQuery } from "../queries";
import { fieldClass, saveClass } from "../rail/RailSection";

export function CommentList({ taskId }: { taskId: string }) {
  const comments = useQuery(commentsQuery(taskId));
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const id = useId();
  const field = useRef<HTMLTextAreaElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const add = useWrite<{ body: string; idempotencyKey?: string }, unknown>({
    mutationFn: (v) =>
      apiWrite({
        kind: "create",
        method: "POST",
        path: `/tasks/${taskId}/comments`,
        body: { body_md: v.body },
        idempotencyKey: v.idempotencyKey,
        schema: zCommentOut,
      }),
    onSuccess: () => {
      setText("");
      // Back to the field, unless the user has moved on to another control meanwhile.
      const at = document.activeElement;
      if (at === null || at === document.body || form.current?.contains(at))
        field.current?.focus();
    },
    onError: () => {
      uiStore.trigger.showNotice({
        text: "Could not add the comment. Try again.",
      });
    },
    onSettled: () => invalidateTaskViews(queryClient),
  });
  const items = comments.data?.items ?? [];
  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-2">
      <h3 id={`${id}-title`} className="text-sm font-semibold">
        Comments
      </h3>
      {items.length === 0 ? (
        <p className="text-sm text-muted">
          {comments.isPending ? "Loading…" : "No comments yet"}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((comment) => (
            <li
              key={comment.id}
              className="rounded-md bg-surface-muted px-3 py-2 text-sm"
            >
              <p className="whitespace-pre-wrap">{comment.body_md}</p>
            </li>
          ))}
        </ul>
      )}
      <form
        ref={form}
        className="flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (!add.isPending && text.trim() !== "")
            add.mutate({ body: text.trim() });
        }}
      >
        <label htmlFor={`${id}-new`} className="sr-only">
          Comment
        </label>
        <textarea
          ref={field}
          id={`${id}-new`}
          rows={2}
          placeholder="Add a comment"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
          }}
          className={fieldClass}
        />
        <button
          type="submit"
          aria-disabled={add.isPending}
          className={`${saveClass} aria-disabled:cursor-not-allowed aria-disabled:opacity-60`}
        >
          Add comment
        </button>
      </form>
    </section>
  );
}

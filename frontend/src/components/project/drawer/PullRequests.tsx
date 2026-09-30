// A task's pull requests (P2-13, FR-12.1): each with its state, checks and review, and a
// field to link another. Opening the drawer asks the API for a fresh read; the worker's
// answer arrives over /ws as a change of the task, which refetches this list.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { tasksListPullRequestsOptions } from "../../../api/@tanstack/react-query.gen";
import { zPullRequestOut } from "../../../api/zod.gen";
import { ApiError, apiWrite, useWrite } from "../../../lib/fetch";
import { PrStatus } from "../../common/PrStatus";
import { fieldClass, saveClass } from "../rail/RailSection";

const PROBLEM_WORDS: Partial<Record<string, string>> = {
  not_a_pull_request: "That is not a github.com pull request link.",
  repo_not_allowed:
    "That repository is not on the allow-list in Settings > GitHub.",
};

export function PullRequests({ taskId }: { taskId: string }) {
  const options = tasksListPullRequestsOptions({ path: { task_id: taskId } });
  const list = useQuery(options);
  const queryClient = useQueryClient();
  const [url, setUrl] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const id = useId();
  const link = useWrite<{ url: string; idempotencyKey?: string }, unknown>({
    mutationFn: (v) =>
      apiWrite({
        kind: "create",
        method: "POST",
        path: `/tasks/${taskId}/pull-requests`,
        body: { url: v.url },
        idempotencyKey: v.idempotencyKey,
        schema: zPullRequestOut,
      }),
    onSuccess: () => {
      setUrl("");
      setProblem(null);
    },
    onError: (error) => {
      const known =
        error instanceof ApiError
          ? PROBLEM_WORDS[error.problem.code]
          : undefined;
      setProblem(known ?? "Could not link the pull request. Try again.");
    },
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: options.queryKey }),
  });
  const items = list.data ?? [];
  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-2">
      <h3 id={`${id}-title`} className="text-sm font-semibold">
        Pull requests
      </h3>
      {items.length === 0 ? (
        <p className="text-sm text-muted">
          {list.isPending ? "Loading…" : "No pull requests linked"}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((pr) => (
            <li
              key={pr.artifact_id}
              className="flex flex-col rounded-md bg-surface-muted px-3 py-1 text-sm"
            >
              {pr.title && <span className="break-words">{pr.title}</span>}
              <PrStatus pr={pr} />
            </li>
          ))}
        </ul>
      )}
      <form
        className="flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (url.trim() !== "") link.mutate({ url: url.trim() });
        }}
      >
        <label htmlFor={`${id}-url`} className="sr-only">
          Pull request link
        </label>
        <input
          id={`${id}-url`}
          type="url"
          inputMode="url"
          placeholder="Link a pull request (https://github.com/…)"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
          }}
          className={fieldClass}
        />
        {problem && (
          <p role="alert" className="text-sm text-danger">
            {problem}
          </p>
        )}
        <button type="submit" disabled={link.isPending} className={saveClass}>
          Link pull request
        </button>
      </form>
    </section>
  );
}

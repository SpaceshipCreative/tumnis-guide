// The project rail's Settings section (P1-02, Data flow rule 6): the local decisions only
// switch. Turning it on keeps every decision about the project's items on the local vLLM
// model; nothing is sent to Jev. A toggle sends one PATCH /v1/projects/{id} with the
// version it read; a 409 shows the current value with a notice (REL-2).
import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type * as z from "zod";

import { projectsGetProjectQueryKey } from "../../../api/@tanstack/react-query.gen";
import type { ProjectOut } from "../../../api/types.gen";
import { zProjectOut } from "../../../api/zod.gen";
import {
  ApiError,
  apiWrite,
  ConflictError,
  useWrite,
} from "../../../lib/fetch";

type Shown = Pick<ProjectOut, "id" | "version" | "local_decisions_only">;
type Saved = z.infer<typeof zProjectOut>;

export function SettingsSection({ project }: { project: Shown }) {
  const queryClient = useQueryClient();
  const ids = { label: useId(), hint: useId() };
  const [saved, setSaved] = useState({
    localOnly: project.local_decisions_only ?? false,
    version: project.version,
  });
  const [message, setMessage] = useState<string | null>(null);

  function show(current: Saved) {
    queryClient.setQueryData(
      projectsGetProjectQueryKey({ path: { project_id: project.id } }),
      current,
    );
    setSaved({
      localOnly: current.local_decisions_only,
      version: current.version,
    });
  }

  const save = useWrite<
    { local_decisions_only: boolean; version: number; idempotencyKey?: string },
    Saved
  >({
    mutationFn: ({ version, idempotencyKey, ...body }) =>
      apiWrite({
        kind: "update",
        method: "PATCH",
        path: `/projects/${project.id}`,
        body,
        version,
        idempotencyKey,
        schema: zProjectOut,
      }),
    onSuccess: show,
    onError: (error) => {
      if (error instanceof ConflictError) {
        const current = zProjectOut.safeParse(error.current);
        if (current.success) {
          show(current.data);
          setMessage(
            "This project changed elsewhere; you are now seeing the current setting.",
          );
          return;
        }
      }
      setMessage(
        error instanceof ApiError
          ? (error.problem.detail ?? error.message)
          : "The setting could not be saved.",
      );
    },
  });

  function toggle() {
    setMessage(null);
    save.mutate({
      local_decisions_only: !saved.localOnly,
      version: saved.version,
    });
  }

  return (
    <section
      aria-label="Project settings"
      className="flex min-w-0 flex-col gap-2"
    >
      <div className="flex min-w-0 items-center justify-between gap-3">
        <span id={ids.label} className="text-sm font-medium">
          Local decisions only
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={saved.localOnly}
          aria-labelledby={ids.label}
          aria-describedby={ids.hint}
          disabled={save.isPending}
          onClick={toggle}
          className={`relative inline-flex min-h-11 min-w-16 shrink-0 items-center rounded-full border border-border px-1 disabled:opacity-60 ${
            saved.localOnly ? "bg-accent" : "bg-surface"
          }`}
        >
          <span
            aria-hidden="true"
            className={`h-7 w-7 rounded-full bg-text transition-transform ${
              saved.localOnly ? "translate-x-7" : "translate-x-0"
            }`}
          />
        </button>
      </div>
      <p id={ids.hint} className="text-sm text-muted">
        Decisions about this project&apos;s items run on the local model and are
        never sent to Jev. They are judged more strictly, so more of them come
        to you for review.
      </p>
      {message ? (
        <p role="status" className="text-sm text-muted">
          {message}
        </p>
      ) : null}
    </section>
  );
}

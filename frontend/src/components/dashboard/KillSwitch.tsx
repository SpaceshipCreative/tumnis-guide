// The kill switch (P2-09, SAF-4): one control pauses every agent (`scope` workspace), or
// one project's (`PauseControl` with a project). Pausing asks for a reason in a confirm
// dialog (`POST /v1/agents/pause`, or `/v1/projects/{id}/pause`); while paused a banner
// says why, and Resume (`.../resume`) is the human act that lets the agents run again (no
// key or tool resumes). The state is `GET /v1/agents/pause`, a live query (`agent_pause`),
// so another tab's or the master's pause shows here too.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type SyntheticEvent } from "react";

import { agentsGetPausesQueryKey } from "../../api/@tanstack/react-query.gen";
import type { OpenPause, PauseOut, ResumeOut } from "../../api/types.gen";
import { apiWrite, useWrite } from "../../lib/fetch";
import { dialogKeyDown } from "../../lib/focusTrap";
import {
  BUTTON_DANGER,
  BUTTON_SECONDARY,
  DIALOG_BACKDROP,
  DIALOG_PANEL,
  DIALOG_TITLE,
  ERROR_TEXT,
  FIELD,
  FIELD_LABEL,
  HINT,
} from "../common/ui";
import { pausesQuery } from "./queries";

interface Labels {
  pause: string; // the control and the dialog's title
  paused: string; // the banner's name
  resume: string;
  explain: string; // what pausing does, in the dialog
}

const WORKSPACE: Labels = {
  pause: "Pause all agents",
  paused: "Agents paused",
  resume: "Resume agents",
  explain:
    "Running agents are stopped and nothing new starts until a person resumes them here.",
};

const PROJECT: Labels = {
  pause: "Pause this project's agents",
  paused: "Project agents paused",
  resume: "Resume project agents",
  explain:
    "This project's running agents are stopped and none start until a person resumes them here.",
};

function PauseDialog({
  labels,
  busy,
  error,
  onPause,
  onCancel,
}: {
  labels: Labels;
  busy: boolean;
  error: string | null;
  onPause: (reason: string) => void;
  onCancel: () => void;
}) {
  const titleId = useId();
  const [reason, setReason] = useState("");
  const field = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    field.current?.focus();
  }, []);
  const trimmed = reason.trim();
  function submit(event: SyntheticEvent) {
    event.preventDefault();
    if (trimmed) onPause(trimmed);
  }
  return (
    <div className={DIALOG_BACKDROP}>
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={DIALOG_PANEL}
        onSubmit={submit}
        onKeyDown={(event) => {
          dialogKeyDown(event, onCancel);
        }}
      >
        <h2 id={titleId} className={DIALOG_TITLE}>
          {labels.pause}
        </h2>
        <p className={HINT}>{labels.explain}</p>
        <label className={FIELD_LABEL}>
          Reason
          <textarea
            ref={field}
            className={FIELD}
            rows={2}
            maxLength={500}
            value={reason}
            onChange={(event) => {
              setReason(event.target.value);
            }}
          />
        </label>
        {error && (
          <p role="alert" className={ERROR_TEXT}>
            {error}
          </p>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className={BUTTON_SECONDARY} onClick={onCancel}>
            Cancel
          </button>
          <button
            type="submit"
            className={BUTTON_DANGER}
            disabled={busy || trimmed === ""}
          >
            Pause
          </button>
        </div>
      </form>
    </div>
  );
}

function PausedBanner({
  labels,
  pause,
  busy,
  error,
  onResume,
}: {
  labels: Labels;
  pause: OpenPause;
  busy: boolean;
  error: string | null;
  onResume: () => void;
}) {
  return (
    <div
      role="status"
      aria-label={labels.paused}
      className="flex min-w-0 flex-wrap items-center gap-2 rounded-xl border border-warning bg-warning-soft px-3 py-2 text-sm"
    >
      <p className="min-w-0 flex-1 break-words">
        <span className="font-semibold text-warning">{labels.paused}: </span>
        {pause.reason}
      </p>
      <button
        type="button"
        className={BUTTON_SECONDARY}
        disabled={busy}
        onClick={onResume}
      >
        {labels.resume}
      </button>
      {error && <p className={`w-full ${ERROR_TEXT}`}>{error}</p>}
    </div>
  );
}

/** Pause and resume one scope: the workspace, or the project `projectId` names. */
export function PauseControl({ projectId }: { projectId?: string }) {
  const labels = projectId === undefined ? WORKSPACE : PROJECT;
  const queryClient = useQueryClient();
  const pauses = useQuery(pausesQuery()); // prefetched by the dashboard's loader
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const base =
    projectId === undefined
      ? "/agents"
      : `/projects/${encodeURIComponent(projectId)}`;
  const scope = projectId === undefined ? "workspace" : "project";
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: agentsGetPausesQueryKey() });

  const pause = useWrite<{ reason: string; idempotencyKey?: string }, PauseOut>(
    {
      mutationFn: ({ reason, idempotencyKey }) =>
        apiWrite<PauseOut>({
          kind: "create",
          method: "POST",
          path: `${base}/pause`,
          body: projectId === undefined ? { scope, reason } : { reason },
          idempotencyKey,
        }),
      onSuccess: async () => {
        setError(null);
        setAsking(false);
        await refresh();
      },
      onError: (failure: Error) => {
        setError(failure.message);
      },
    },
  );
  const resume = useWrite<{ idempotencyKey?: string }, ResumeOut>({
    mutationFn: ({ idempotencyKey }) =>
      apiWrite<ResumeOut>({
        kind: "create",
        method: "POST",
        path: `${base}/resume`,
        body: projectId === undefined ? { scope } : {},
        idempotencyKey,
      }),
    onSuccess: async () => {
      setError(null);
      await refresh();
    },
    onError: (failure: Error) => {
      setError(failure.message);
    },
  });

  if (!pauses.data) return null;
  const open =
    projectId === undefined
      ? pauses.data.workspace
      : (pauses.data.projects.find((p) => p.project_id === projectId) ?? null);
  if (open) {
    return (
      <PausedBanner
        labels={labels}
        pause={open}
        busy={resume.isPending}
        error={error}
        onResume={() => {
          resume.mutate({});
        }}
      />
    );
  }
  return (
    <>
      <button
        type="button"
        className={BUTTON_DANGER}
        onClick={() => {
          setError(null);
          setAsking(true);
        }}
      >
        {labels.pause}
      </button>
      {asking && (
        <PauseDialog
          labels={labels}
          busy={pause.isPending}
          error={error}
          onPause={(reason) => {
            pause.mutate({ reason });
          }}
          onCancel={() => {
            setAsking(false);
          }}
        />
      )}
    </>
  );
}

/** The dashboard's kill switch: pause every agent. */
export function KillSwitch() {
  return <PauseControl />;
}

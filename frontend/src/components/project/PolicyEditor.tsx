// The project's approval policy editor (P2-05, FR-5.6), in the Settings rail section. Each
// action class is a switch: on, the agent asks for approval before it acts (gated); off,
// it may act without asking (allowed). The list is the union of the stored gated and
// allowed lists, so a class a project added still shows. Save sends one
// PUT /v1/projects/{id}/policy with both lists and the version read; a 409 shows the
// policy as it is now with a notice, and the next save sends that version (REL-2).
// Unknown actions are not listed: the server asks for approval unless the approval-need
// check is confident they are safe. Switches are 44 px tall, for one-handed use (UX 11).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type * as z from "zod";

import { zPolicyOut } from "../../api/zod.gen";
import { ApiError, apiWrite, ConflictError, useWrite } from "../../lib/fetch";
import { BUTTON_SECONDARY, HINT } from "../common/ui";
import { policyQuery } from "./queries";
import type { Project } from "./types";

type Policy = z.infer<typeof zPolicyOut>;

/** An action class as words: `push_feature_branch` reads "Push feature branch". */
export function actionText(action: string): string {
  const words = action.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function classesOf(policy: Policy): string[] {
  return [...new Set([...policy.gated, ...policy.allowed])].sort((a, b) =>
    actionText(a).localeCompare(actionText(b)),
  );
}

function ActionSwitch({
  action,
  gated,
  disabled,
  onToggle,
}: {
  action: string;
  gated: boolean;
  disabled: boolean;
  onToggle: () => void;
}) {
  const labelId = useId();
  return (
    <li className="flex min-w-0 items-center justify-between gap-3">
      <span id={labelId} className="min-w-0 text-sm break-words">
        {actionText(action)}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={gated}
        aria-labelledby={labelId}
        disabled={disabled}
        onClick={onToggle}
        className={`relative inline-flex min-h-11 min-w-16 shrink-0 items-center rounded-full border border-border px-1 disabled:opacity-60 ${
          gated ? "bg-accent" : "bg-surface"
        }`}
      >
        <span
          aria-hidden="true"
          className={`h-7 w-7 rounded-full bg-text transition-transform ${
            gated ? "translate-x-7" : "translate-x-0"
          }`}
        />
      </button>
    </li>
  );
}

function PolicyForm({
  project,
  policy,
  onSaved,
  onConflict,
  onError,
}: {
  project: Project;
  policy: Policy;
  onSaved: (saved: Policy) => void;
  onConflict: (current: Policy) => void;
  onError: (message: string) => void;
}) {
  const hintId = useId();
  const classes = classesOf(policy);
  const [gated, setGated] = useState(() => new Set(policy.gated));
  const changed = classes.some(
    (action) => gated.has(action) !== policy.gated.includes(action),
  );

  const save = useWrite<
    {
      gated: string[];
      allowed: string[];
      version: number;
      idempotencyKey?: string;
    },
    Policy
  >({
    mutationFn: ({ version, idempotencyKey, ...body }) =>
      apiWrite({
        kind: "update",
        method: "PUT",
        path: `/projects/${project.id}/policy`,
        body,
        version,
        idempotencyKey,
        schema: zPolicyOut,
      }),
    onSuccess: onSaved,
    onError: (error) => {
      if (error instanceof ConflictError) {
        const current = zPolicyOut.safeParse(error.current);
        if (current.success) {
          onConflict(current.data);
          return;
        }
      }
      onError(
        error instanceof ApiError
          ? (error.problem.detail ?? error.message)
          : "The policy could not be saved.",
      );
    },
  });

  return (
    <form
      className="flex min-w-0 flex-col gap-2"
      aria-describedby={hintId}
      onSubmit={(event) => {
        event.preventDefault();
        if (!changed || save.isPending) return;
        save.mutate({
          gated: classes.filter((action) => gated.has(action)),
          allowed: classes.filter((action) => !gated.has(action)),
          version: policy.version,
        });
      }}
    >
      <p id={hintId} className={HINT}>
        On: the agent asks you before it does this. Off: it may do it without
        asking. Actions not listed always ask, unless Tumnis is confident they
        are safe.
      </p>
      <ul aria-label="Action classes" className="flex flex-col gap-1">
        {classes.map((action) => (
          <ActionSwitch
            key={action}
            action={action}
            gated={gated.has(action)}
            disabled={save.isPending}
            onToggle={() => {
              setGated((current) => {
                const next = new Set(current);
                if (next.has(action)) next.delete(action);
                else next.add(action);
                return next;
              });
            }}
          />
        ))}
      </ul>
      <button
        type="submit"
        disabled={!changed || save.isPending}
        className={BUTTON_SECONDARY}
      >
        Save policy
      </button>
    </form>
  );
}

export function PolicyEditor({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const query = useQuery(policyQuery(project.id));
  const [message, setMessage] = useState<string | null>(null);
  const headingId = useId();

  function show(policy: Policy) {
    queryClient.setQueryData(policyQuery(project.id).queryKey, policy);
  }

  return (
    <section
      aria-labelledby={headingId}
      className="flex min-w-0 flex-col gap-2 border-t border-border pt-3"
    >
      <h3 id={headingId} className="text-sm font-medium">
        Approval policy
      </h3>
      {query.isPending && <p className={HINT}>Loading the policy…</p>}
      {query.isError && (
        <p role="alert" className="text-sm text-danger">
          The policy could not be loaded.
        </p>
      )}
      {query.data && (
        // A new version (saved here, changed elsewhere, or the current one after a
        // conflict) starts the form over from it.
        <PolicyForm
          key={query.data.version}
          project={project}
          policy={query.data}
          onSaved={(saved) => {
            show(saved);
            setMessage("Policy saved.");
          }}
          onConflict={(current) => {
            show(current);
            setMessage(
              "This policy changed elsewhere; you are now seeing the current policy.",
            );
          }}
          onError={setMessage}
        />
      )}
      {message ? (
        <p role="status" className="text-sm text-muted">
          {message}
        </p>
      ) : null}
    </section>
  );
}

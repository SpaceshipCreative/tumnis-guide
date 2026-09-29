// Settings > Agents (P1-04, FR-5.9): the runner daemons (online from their heartbeats,
// offline after three missed ones) and the Hermes profiles with their last health check.
// A new runner's device token is shown once, in a dialog, and never cached.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import type {
  AgentProfileOut,
  HealthCheckAccepted,
  ProfileHealth,
  RunnerCreated,
  RunnerOut,
} from "../../api/types.gen";
import { ApiError, apiWrite, useWrite } from "../../lib/fetch";
import { profilesQuery, runnersQuery } from "./queries";
import {
  BUTTON,
  CARD,
  ERROR,
  HEADING,
  HINT,
  INPUT,
  LABEL,
  SECONDARY,
  SECTION,
} from "./styles";

const CHIP =
  "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium";
const GOOD = `${CHIP} bg-accent text-accent-contrast`;
const BAD = `${CHIP} border border-danger text-danger`;
const NEUTRAL = `${CHIP} bg-surface-muted text-muted`;

const RUNNER_STATUS: Record<RunnerOut["status"], [string, string]> = {
  online: ["Online", GOOD],
  offline: ["Offline", BAD],
  never_seen: ["Never connected", NEUTRAL],
};

function healthChip(health: ProfileHealth | null): [string, string] {
  if (!health) return ["Not checked", NEUTRAL];
  if (!health.reachable) return ["Unreachable", BAD];
  if (health.status === "unsupported") return ["Unsupported", NEUTRAL];
  if (health.status === "ok") return ["Healthy", GOOD];
  return ["Needs attention", BAD];
}

function authChip(health: ProfileHealth | null): [string, string] | null {
  if (health?.authenticated === true) return ["Authenticated", GOOD];
  if (health?.authenticated === false) return ["Not signed in", BAD];
  return null;
}

function problemText(error: unknown): string {
  return error instanceof ApiError
    ? (error.problem.detail ?? error.message)
    : "That did not work.";
}

function invalidate(
  queryClient: ReturnType<typeof useQueryClient>,
  id: string,
): void {
  void queryClient.invalidateQueries({
    predicate: (query) =>
      (query.queryKey[0] as { _id?: string } | undefined)?._id === id,
  });
}

function RunnerItem({ runner }: { runner: RunnerOut }) {
  const nameId = useId();
  const [label, chip] = RUNNER_STATUS[runner.status];
  const facts = [
    runner.host,
    runner.os,
    runner.daemon_version && `daemon ${runner.daemon_version}`,
    runner.hermes_version && `Hermes ${runner.hermes_version}`,
  ].filter(Boolean);
  return (
    <li aria-labelledby={nameId} className={CARD}>
      <div className="flex flex-wrap items-center gap-2">
        <p id={nameId} className="font-medium [overflow-wrap:anywhere]">
          {runner.name}
        </p>
        <span className={chip}>{label}</span>
      </div>
      {facts.length > 0 && (
        <p className="text-sm [overflow-wrap:anywhere]">{facts.join(" · ")}</p>
      )}
      <p className={HINT}>
        {runner.last_heartbeat_at
          ? `Last heartbeat ${new Date(runner.last_heartbeat_at).toLocaleString()}`
          : "No heartbeat yet"}
        {runner.profiles.length > 0 &&
          ` · profiles: ${runner.profiles.join(", ")}`}
      </p>
    </li>
  );
}

function ProfileItem({
  profile,
  onCheck,
  checking,
}: {
  profile: AgentProfileOut;
  onCheck: (profile: AgentProfileOut) => void;
  checking: boolean;
}) {
  const nameId = useId();
  const [label, chip] = healthChip(profile.health);
  const auth = authChip(profile.health);
  const version = profile.health?.version;
  return (
    <li aria-labelledby={nameId} className={CARD}>
      <div className="flex flex-wrap items-center gap-2">
        <p id={nameId} className="font-medium [overflow-wrap:anywhere]">
          {profile.name}
        </p>
        <span className={chip}>{label}</span>
        {auth && <span className={auth[1]}>{auth[0]}</span>}
      </div>
      <p className="text-sm">
        {profile.role === "master" ? "Master agent" : "Project agent"} ·{" "}
        {profile.transport === "daemon" ? "runner daemon" : "MCP endpoint"}
      </p>
      {version && <p className="text-sm">Hermes {version}</p>}
      {profile.health?.error && (
        <p className="text-sm text-danger [overflow-wrap:anywhere]">
          {profile.health.error}
        </p>
      )}
      <p className={HINT}>
        {profile.health_checked_at
          ? `Checked ${new Date(profile.health_checked_at).toLocaleString()}`
          : "Never checked"}
      </p>
      <div>
        <button
          type="button"
          className={SECONDARY}
          disabled={checking}
          onClick={() => {
            onCheck(profile);
          }}
        >
          Check health
        </button>
      </div>
    </li>
  );
}

function TokenDialog({
  token,
  onClose,
}: {
  token: string;
  onClose: () => void;
}) {
  const titleId = useId();
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex w-full max-w-md flex-col gap-3 rounded-lg bg-surface p-4 shadow-lg"
      >
        <h3 id={titleId} className="text-lg font-semibold">
          Copy the runner's token
        </h3>
        <p>
          It is shown only now. Put it in the daemon's token file on the runner
          before closing.
        </p>
        <code className="rounded bg-surface-muted p-2 text-sm [overflow-wrap:anywhere]">
          {token}
        </code>
        <div className="flex justify-end">
          <button type="button" className={BUTTON} onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

export function AgentsSection() {
  const queryClient = useQueryClient();
  const nameId = useId();
  const [name, setName] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const runners = useQuery(runnersQuery());
  const profiles = useQuery(profilesQuery());

  const addRunner = useWrite<
    { name: string; idempotencyKey?: string },
    RunnerCreated
  >({
    mutationFn: ({ name: runnerName, idempotencyKey }) =>
      apiWrite<RunnerCreated>({
        kind: "create",
        method: "POST",
        path: "/runners",
        body: { name: runnerName },
        idempotencyKey,
      }),
    onSuccess: (created) => {
      setName("");
      setToken(created.token);
    },
    onError: (error) => {
      setMessage(problemText(error));
    },
    onSettled: () => {
      invalidate(queryClient, "agentsListRunners");
    },
  });

  const check = useWrite<
    { profile: AgentProfileOut; idempotencyKey?: string },
    HealthCheckAccepted
  >({
    mutationFn: ({ profile, idempotencyKey }) =>
      apiWrite<HealthCheckAccepted>({
        kind: "create",
        method: "POST",
        path: `/agents/profiles/${encodeURIComponent(profile.id)}/health-check`,
        idempotencyKey,
      }),
    onSuccess: (_accepted, { profile }) => {
      setMessage(`Checking ${profile.name}…`);
    },
    onError: (error) => {
      setMessage(problemText(error));
    },
  });

  const runnerItems = runners.data?.items ?? [];
  const profileItems = profiles.data?.items ?? [];
  return (
    <section aria-labelledby="agents-title" className={SECTION}>
      <h2 id="agents-title" className={HEADING}>
        Agents
      </h2>
      <p className={HINT}>
        Runners are the daemons on your machines that run Hermes; each profile
        is an agent Tumnis may send work to.
      </p>
      <p role="status" className={HINT}>
        {message}
      </p>

      <h3 className="text-lg font-semibold">Runners</h3>
      {runners.isError && (
        <p role="alert" className={ERROR}>
          The runners could not be loaded.
        </p>
      )}
      {runners.isSuccess && runnerItems.length === 0 && (
        <p className={HINT}>No runners yet.</p>
      )}
      {runnerItems.length > 0 && (
        <ul aria-label="Runners" className="flex flex-col gap-2">
          {runnerItems.map((runner) => (
            <RunnerItem key={runner.id} runner={runner} />
          ))}
        </ul>
      )}
      <form
        className="flex flex-col gap-2 sm:flex-row sm:items-end"
        onSubmit={(event) => {
          event.preventDefault();
          setMessage(null);
          if (name.trim()) addRunner.mutate({ name: name.trim() });
        }}
      >
        <div className={`${LABEL} min-w-0 flex-1`}>
          <label htmlFor={nameId}>Runner name</label>
          <input
            id={nameId}
            className={INPUT}
            value={name}
            pattern="[a-z0-9][a-z0-9\-]{0,62}"
            autoComplete="off"
            onChange={(e) => {
              setName(e.target.value);
            }}
          />
        </div>
        <button type="submit" className={BUTTON} disabled={addRunner.isPending}>
          Add runner
        </button>
      </form>

      <h3 className="text-lg font-semibold">Agent profiles</h3>
      {profiles.isError && (
        <p role="alert" className={ERROR}>
          The agent profiles could not be loaded.
        </p>
      )}
      {profiles.isSuccess && profileItems.length === 0 && (
        <p className={HINT}>No agent profiles yet.</p>
      )}
      {profileItems.length > 0 && (
        <ul aria-label="Agent profiles" className="flex flex-col gap-2">
          {profileItems.map((profile) => (
            <ProfileItem
              key={profile.id}
              profile={profile}
              checking={check.isPending}
              onCheck={(chosen) => {
                setMessage(null);
                check.mutate({ profile: chosen });
              }}
            />
          ))}
        </ul>
      )}
      {token && (
        <TokenDialog
          token={token}
          onClose={() => {
            setToken(null);
          }}
        />
      )}
    </section>
  );
}

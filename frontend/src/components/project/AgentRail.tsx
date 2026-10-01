// The Agent section of the project's Context rail (P2-17, FR-2.7): one line names the
// project's agent profile and its health; opened in place it shows the Hermes version,
// the workers and tools the profile declared at its last check (read only once the
// section opens) and the pause control the page passes in.
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import type { ProfileHealth } from "../../api/types.gen";
import { profileToolsQuery, projectProfileQuery } from "../settings/queries";
import { RailSection } from "./rail/RailSection";

export function healthWord(health: ProfileHealth | null): string {
  if (!health) return "Not checked yet";
  if (!health.reachable) return "Offline";
  if (health.status === "ok") return "Healthy";
  if (health.status === "degraded") return "Degraded";
  return "Warning";
}

export function AgentRail({
  projectId,
  open,
  onToggle,
  pause,
}: {
  projectId: string;
  open: boolean;
  onToggle: () => void;
  pause?: ReactNode;
}) {
  const profiles = useQuery(projectProfileQuery(projectId));
  const profile = profiles.data?.items.find(
    (p) => p.role === "project" && p.project_id === projectId,
  );
  const tools = useQuery({
    ...profileToolsQuery(profile?.id ?? ""),
    enabled: open && profile !== undefined,
  });
  const summary = profiles.isPending
    ? "Loading…"
    : profiles.isError
      ? "The agent could not be loaded"
      : profile
        ? `${profile.name} · ${healthWord(profile.health)}`
        : "No agent yet";
  const version = profile?.health?.version;
  const servers = tools.data?.servers ?? [];
  return (
    <RailSection
      title="Agent"
      summary={summary}
      open={open}
      onToggle={onToggle}
    >
      {profile ? (
        <>
          <p className="text-sm">
            {version ? `Hermes ${version}` : "Hermes version not known yet"}
          </p>
          {tools.isPending ? (
            <p className="text-sm text-muted">Loading the tools…</p>
          ) : tools.isError ? (
            <p role="alert" className="text-sm text-danger">
              The tools could not be loaded.
            </p>
          ) : servers.length === 0 ? (
            <p className="text-sm text-muted">No workers or tools reported.</p>
          ) : (
            <ul
              aria-label="Workers and tools"
              className="flex flex-col gap-1 text-sm"
            >
              {servers.map((server) => (
                <li key={server.name} className="flex justify-between gap-2">
                  <span className="min-w-0 truncate">{server.name}</span>
                  {!server.allowed && (
                    <span className="shrink-0 text-danger">Not allowed</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <p className="text-sm text-muted">
          The project gets its agent once a runner provisions it.
        </p>
      )}
      {pause}
    </RailSection>
  );
}

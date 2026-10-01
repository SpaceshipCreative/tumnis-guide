// The project's agent pause (P2-09, SAF-4) in the Context rail: pause this project's
// agents with a reason, or resume them. A small seam until P2-17's agent rail section
// mounts the same `PauseControl`.
import { useQuery } from "@tanstack/react-query";

import { agentsGetPausesOptions } from "../../../api/@tanstack/react-query.gen";
import { PauseControl } from "../../dashboard/KillSwitch";
import { RailSection } from "./RailSection";

export function AgentPauseSection({
  projectId,
  open,
  onToggle,
}: {
  projectId: string;
  open: boolean;
  onToggle: () => void;
}) {
  const pauses = useQuery(agentsGetPausesOptions());
  const summary = pauses.data?.workspace
    ? "All agents paused"
    : pauses.data?.projects.some((p) => p.project_id === projectId)
      ? "Paused"
      : "Running";
  return (
    <RailSection
      title="Agent pause"
      summary={summary}
      open={open}
      onToggle={onToggle}
    >
      <PauseControl projectId={projectId} />
    </RailSection>
  );
}

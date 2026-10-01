// The Context sections (P0-24, FR-2.7), shared by the laptop rail and the phone sheet;
// one opens at a time. Knowledge and Agent join with P1-17 and P1-06; the project's agent
// pause (P2-09) sits last until P2-17's agent section takes it.
import { useState } from "react";

import type { Project } from "../types";
import { AgentPauseSection } from "./AgentPauseSection";
import { BriefSection } from "./BriefSection";
import { ConnectionsSection } from "./ConnectionsSection";
import { ScheduleSection } from "./ScheduleSection";
import { SettingsSection } from "./SettingsSection";

type Section = "brief" | "connections" | "schedule" | "settings" | "pause";

export function RailSections({
  project,
  onOpenTask,
}: {
  project: Project;
  onOpenTask: (taskId: string) => void;
}) {
  const [open, setOpen] = useState<Section | null>(null);
  const toggle = (section: Section) => () => {
    setOpen((current) => (current === section ? null : section));
  };
  return (
    <div className="flex flex-col">
      <BriefSection
        projectId={project.id}
        open={open === "brief"}
        onToggle={toggle("brief")}
      />
      <ConnectionsSection
        project={project}
        open={open === "connections"}
        onToggle={toggle("connections")}
      />
      <ScheduleSection
        projectId={project.id}
        open={open === "schedule"}
        onToggle={toggle("schedule")}
        onOpenTask={onOpenTask}
      />
      <SettingsSection
        project={project}
        open={open === "settings"}
        onToggle={toggle("settings")}
      />
      <AgentPauseSection
        projectId={project.id}
        open={open === "pause"}
        onToggle={toggle("pause")}
      />
    </div>
  );
}

// The Context sections (P0-24, FR-2.7), shared by the laptop rail and the phone sheet;
// one opens at a time. Knowledge (P1-17) lists and edits the project's documents; Agent
// (P2-17) shows the project's profile, health and tools, and holds the project's agent
// pause (P2-09, SAF-4). Folder (P3-14) holds the existing-folder setup and the move.
import { useState } from "react";

import { PauseControl } from "../../dashboard/KillSwitch";
import { AgentRail } from "../AgentRail";
import { FolderSetup } from "../FolderSetup";
import type { Project } from "../types";
import { BriefSection } from "./BriefSection";
import { ConnectionsSection } from "./ConnectionsSection";
import { KnowledgeSection } from "./KnowledgeSection";
import { RailSection } from "./RailSection";
import { ScheduleSection } from "./ScheduleSection";
import { SettingsSection } from "./SettingsSection";

type Section =
  | "brief"
  | "knowledge"
  | "connections"
  | "schedule"
  | "agent"
  | "settings"
  | "folder";

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
      <KnowledgeSection
        projectId={project.id}
        open={open === "knowledge"}
        onToggle={toggle("knowledge")}
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
      <AgentRail
        projectId={project.id}
        open={open === "agent"}
        onToggle={toggle("agent")}
        pause={<PauseControl projectId={project.id} />}
      />
      <SettingsSection
        project={project}
        open={open === "settings"}
        onToggle={toggle("settings")}
      />
      <RailSection
        title="Folder"
        summary="Where the project's files live"
        open={open === "folder"}
        onToggle={toggle("folder")}
      >
        <FolderSetup projectId={project.id} />
      </RailSection>
    </div>
  );
}

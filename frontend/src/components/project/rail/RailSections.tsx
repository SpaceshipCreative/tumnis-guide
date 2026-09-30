// The Context sections (P0-24, FR-2.7), shared by the laptop rail and the phone sheet;
// one opens at a time. Knowledge and Agent join with P1-17 and P1-06; Folder (P3-14)
// holds the existing-folder setup and the move.
import { useState } from "react";

import { FolderSetup } from "../FolderSetup";
import type { Project } from "../types";
import { BriefSection } from "./BriefSection";
import { ConnectionsSection } from "./ConnectionsSection";
import { RailSection } from "./RailSection";
import { ScheduleSection } from "./ScheduleSection";
import { SettingsSection } from "./SettingsSection";

type Section = "brief" | "connections" | "schedule" | "settings" | "folder";

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

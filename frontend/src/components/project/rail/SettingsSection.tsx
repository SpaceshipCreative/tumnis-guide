// The project rail's Settings section (P1-02): the local decisions only switch. Filled
// in by P1-02's implementation; the spec test (T-P1-02-16) is committed first.
import type { ProjectOut } from "../../../api/types.gen";

export function SettingsSection({
  project,
}: {
  project: Pick<ProjectOut, "id" | "version">;
}) {
  return <section aria-label="Project settings" data-project={project.id} />;
}

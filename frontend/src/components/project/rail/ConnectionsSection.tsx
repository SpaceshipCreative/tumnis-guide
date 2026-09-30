// Connections (P0-24, FR-2.7): the project's people, domains and code location; read at
// a glance, edited in a small form (one per line) with the project's version. Below it,
// the linked Coolify apps with their last deploys, linked and unlinked by UUID (P2-14).
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { deployStatusQuery } from "../../dashboard/queries";
import { workspaceQuery } from "../../settings/queries";
import { CoolifyApps } from "../DeployStatus";
import { useUpdateProject } from "../mutations";
import type { ProjectLinkIn } from "../../../api/types.gen";
import type { Project } from "../types";
import { fieldClass, RailSection, saveClass } from "./RailSection";

type Link = ProjectLinkIn;

const NOUNS: Record<Link["kind"], [string, string]> = {
  person: ["person", "people"],
  domain: ["domain", "domains"],
  repo: ["repository", "repositories"],
  coolify_app: ["app", "apps"],
};

export function connectionsSummary(project: Project): string {
  const parts = (Object.keys(NOUNS) as Link["kind"][]).flatMap((kind) => {
    const n = (project.links ?? []).filter((l) => l.kind === kind).length;
    const [one, many] = NOUNS[kind];
    return n === 0 ? [] : [`${String(n)} ${n === 1 ? one : many}`];
  });
  if (project.code_path) parts.push("code folder");
  return parts.length === 0 ? "No connections yet" : parts.join(" · ");
}

const lines = (text: string) =>
  text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l !== "");

function ConnectionsForm({ project }: { project: Project }) {
  const values = (kind: Link["kind"]) =>
    (project.links ?? [])
      .filter((l) => l.kind === kind)
      .map((l) => l.value)
      .join("\n");
  const [people, setPeople] = useState(values("person"));
  const [domains, setDomains] = useState(values("domain"));
  const [codePath, setCodePath] = useState(project.code_path ?? "");
  const update = useUpdateProject();
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        const kept = (project.links ?? []).filter(
          (l) => l.kind !== "person" && l.kind !== "domain",
        );
        update.mutate({
          project,
          patch: {
            links: [
              ...lines(people).map((value) => ({ kind: "person", value })),
              ...lines(domains).map((value) => ({ kind: "domain", value })),
              ...kept,
            ],
            code_path: codePath.trim() === "" ? null : codePath.trim(),
          },
        });
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        People (one per line)
        <textarea
          rows={3}
          value={people}
          onChange={(e) => {
            setPeople(e.target.value);
          }}
          className={fieldClass}
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Domains (one per line)
        <textarea
          rows={2}
          value={domains}
          onChange={(e) => {
            setDomains(e.target.value);
          }}
          className={fieldClass}
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Code location
        <input
          type="text"
          value={codePath}
          onChange={(e) => {
            setCodePath(e.target.value);
          }}
          className={fieldClass}
        />
      </label>
      <button type="submit" disabled={update.isPending} className={saveClass}>
        Save connections
      </button>
    </form>
  );
}

/** The Coolify apps; the statuses load when the section opens (P2-14, FR-12.2). */
function CoolifyConnections({ project }: { project: Project }) {
  const deployStatus = useQuery(deployStatusQuery());
  const workspace = useQuery(workspaceQuery());
  const apps =
    deployStatus.data?.find((entry) => entry.project_id === project.id)?.apps ??
    [];
  return (
    <section aria-label="Coolify" className="flex flex-col gap-1">
      <h3 className="text-sm font-semibold">Coolify apps</h3>
      <CoolifyApps
        project={project}
        apps={apps}
        timeZone={workspace.data?.timezone ?? "UTC"}
      />
    </section>
  );
}

export function ConnectionsSection({
  project,
  open,
  onToggle,
}: {
  project: Project;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <RailSection
      title="Connections"
      summary={connectionsSummary(project)}
      open={open}
      onToggle={onToggle}
    >
      <ConnectionsForm key={project.version} project={project} />
      <CoolifyConnections project={project} />
    </RailSection>
  );
}

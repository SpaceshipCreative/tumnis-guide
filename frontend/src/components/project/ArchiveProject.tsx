// Archive and unarchive (P2-18, FR-2.1, FR-5.10; APP-13). Archiving hides a project from
// the lists and packs its data away (`archive_state` is `archiving` until the workflow is
// done); unarchiving brings it back at its old place. Both are one POST with the version
// read; a 409 shows the current project with the conflict notice (REL-2). The project's
// Settings section archives (after asking, inline: the section may sit in the phone's
// Context sheet, a modal of its own) and unarchives; the project list has the archived
// projects list with Unarchive; the header shows the state as a badge.
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useId, useState } from "react";

import { projectsListProjectsInfiniteOptions } from "../../api/@tanstack/react-query.gen";
import type { ProjectOut } from "../../api/types.gen";
import { zProjectOut } from "../../api/zod.gen";
import { apiWrite, ConflictError, useWrite } from "../../lib/fetch";
import { queryId } from "../../lib/task-cache";
import { uiStore } from "../../stores/uiStore";
import {
  BUTTON_DANGER,
  BUTTON_QUIET,
  BUTTON_SECONDARY,
  badge,
  HINT,
} from "../common/ui";
import { projectQuery } from "./queries";
import type { Project } from "./types";

type Action = "archive" | "unarchive";

interface ArchiveVars {
  project: Pick<Project, "id" | "version">;
  action: Action;
  idempotencyKey?: string;
}

/** The archive state in words, or null for a live project. */
export function archiveLabel(
  project: Pick<Project, "archived_at" | "archive_state">,
): string | null {
  if (project.archive_state === "unarchiving") return "restoring";
  if (project.archived_at == null) return null;
  return project.archive_state === "archiving" ? "archiving" : "archived";
}

const WORDS: Record<string, string> = {
  archiving: "Archiving",
  archived: "Archived",
  restoring: "Restoring",
};

/** The header's badge: "Archived", "Archiving" or "Restoring"; nothing when live. */
export function ArchiveBadge({
  project,
}: {
  project: Pick<Project, "archived_at" | "archive_state">;
}) {
  const label = archiveLabel(project);
  if (label === null) return null;
  return (
    <span
      role="img"
      aria-label={`Archive: ${label}`}
      className={badge(label === "archived" ? "neutral" : "info")}
    >
      {WORDS[label]}
    </span>
  );
}

/** POST /v1/projects/{id}/archive or /unarchive with the version read. */
export function useArchiveProject() {
  return useWrite<ArchiveVars, ProjectOut>({
    mutationFn: async (v) => {
      const project = await apiWrite({
        kind: "update",
        method: "POST",
        path: `/projects/${v.project.id}/${v.action}`,
        body: {},
        version: v.project.version,
        idempotencyKey: v.idempotencyKey,
        schema: zProjectOut,
      });
      return project as ProjectOut;
    },
    onSuccess: (project, _v, _s, ctx) => {
      ctx.client.setQueryData(projectQuery(project.id).queryKey, project);
    },
    onError: (error, v, _s, ctx) => {
      const current =
        error instanceof ConflictError
          ? zProjectOut.safeParse(error.current)
          : null;
      if (current?.success) {
        ctx.client.setQueryData(
          projectQuery(v.project.id).queryKey,
          current.data as ProjectOut,
        );
        uiStore.trigger.showConflict({ entity: "project" });
      } else {
        uiStore.trigger.showNotice({
          text:
            v.action === "archive"
              ? "The project could not be archived. Try again."
              : "The project could not be unarchived. Try again.",
        });
      }
    },
    // Every project list (with or without the archived ones) and the project itself.
    onSettled: (_d, _e, v, _s, ctx) =>
      ctx.client.invalidateQueries({
        predicate: (query) =>
          queryId(query.queryKey) === "projectsListProjects" ||
          queryId(query.queryKey) ===
            queryId(projectQuery(v.project.id).queryKey),
      }),
  });
}

/** The project Settings section's part: Archive (asks first) or Unarchive. */
export function ArchiveControl({ project }: { project: Project }) {
  const write = useArchiveProject();
  const [asking, setAsking] = useState(false);
  const archived = project.archived_at != null;
  const restoring = project.archive_state === "unarchiving";
  return (
    <div className="flex min-w-0 flex-col gap-2 border-t border-border pt-3">
      <span className="text-sm font-medium">Archive</span>
      {archived ? (
        <>
          <p className={HINT}>
            This project is archived: it is hidden from your lists and its agent
            takes no runs. Unarchive brings it back where it was.
          </p>
          <button
            type="button"
            disabled={write.isPending}
            onClick={() => {
              write.mutate({ project, action: "unarchive" });
            }}
            className={`${BUTTON_SECONDARY} self-start`}
          >
            Unarchive project
          </button>
        </>
      ) : asking ? (
        <div
          role="group"
          aria-label="Archive this project?"
          className="flex flex-col gap-2"
        >
          <p className={HINT}>
            Archive {project.name}? It leaves your lists and its agent stops
            taking runs; nothing is deleted. You can unarchive it here or from
            the archived projects list.
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={write.isPending}
              onClick={() => {
                setAsking(false);
                write.mutate({ project, action: "archive" });
              }}
              className={BUTTON_DANGER}
            >
              Yes, archive
            </button>
            <button
              type="button"
              onClick={() => {
                setAsking(false);
              }}
              className={BUTTON_SECONDARY}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <p className={HINT}>
            {restoring
              ? "This project's data is being restored."
              : "Hide a finished project and pack its data away."}
          </p>
          <button
            type="button"
            disabled={write.isPending || restoring}
            onClick={() => {
              setAsking(true);
            }}
            className={`${BUTTON_SECONDARY} self-start`}
          >
            Archive project
          </button>
        </>
      )}
    </div>
  );
}

const ALL_PROJECTS = { query: { limit: 200, include_archived: true } };

/** The project list's archived projects: shown on request, each with Unarchive. The list
 * has no archived-only filter and archived projects sort among the live ones, so it reads
 * every page of the full list. */
export function ArchivedProjects() {
  const [open, setOpen] = useState(false);
  const listId = useId();
  const all = useInfiniteQuery({
    ...projectsListProjectsInfiniteOptions(ALL_PROJECTS),
    initialPageParam: {},
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: open,
  });
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = all;
  useEffect(() => {
    if (open && hasNextPage && !isFetchingNextPage) void fetchNextPage();
  }, [open, hasNextPage, isFetchingNextPage, fetchNextPage]);
  const write = useArchiveProject();
  const archived = (all.data?.pages ?? [])
    .flatMap((page) => page.items)
    .filter((p) => p.archived_at != null);
  const loaded = all.isSuccess && !hasNextPage;
  return (
    <section className="flex flex-col gap-2">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => {
          setOpen((current) => !current);
        }}
        className={`${BUTTON_QUIET} self-start`}
      >
        {open ? "Hide archived projects" : "Show archived projects"}
      </button>
      {open && (
        <div id={listId} className="flex flex-col gap-2">
          {all.isError && (
            <p role="alert" className="text-sm text-danger">
              Archived projects could not be loaded.
            </p>
          )}
          <ul
            aria-label="Archived projects"
            className="flex flex-col divide-y divide-border rounded-md border border-border bg-surface"
          >
            {loaded && archived.length === 0 && (
              <li className="px-4 py-3 text-sm text-muted">
                No archived projects.
              </li>
            )}
            {archived.map((project) => (
              <li
                key={project.id}
                className="flex flex-wrap items-center justify-between gap-2 px-4 py-2"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <Link
                    to="/projects/$projectId"
                    params={{ projectId: project.id }}
                    className="min-w-0 truncate font-medium"
                  >
                    {project.name}
                  </Link>
                  <ArchiveBadge project={project} />
                </span>
                <button
                  type="button"
                  aria-label={`Unarchive ${project.name}`}
                  disabled={write.isPending}
                  onClick={() => {
                    write.mutate({ project, action: "unarchive" });
                  }}
                  className={BUTTON_QUIET}
                >
                  Unarchive
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

// The task-first project page (P0-24, FR-2.2 to FR-2.8): the header, the composer, the
// Tasks, Board or Calendar (P1-12) view and, on a laptop, the Context rail beside them; on the phone a
// segmented control and the Context sheet. View state lives in the URL (`view`, `task`),
// the last view per project in `uiStore`, server data in Query.
import { useQuery } from "@tanstack/react-query";
import { useSelector } from "@xstate/store-react";
import { type ReactNode, useId, useState } from "react";

import { useIsLaptop } from "../../lib/media";
import { mondayOf } from "../../lib/time";
import type { ProjectView } from "../../lib/views";
import { uiStore } from "../../stores/uiStore";
import { BoardView } from "../board/BoardView";
import { CalendarView } from "./CalendarView";
import { workspaceQuery } from "../settings/queries";
import { Composer } from "./Composer";
import { TaskDrawer } from "./drawer/TaskDrawer";
import { groupOf } from "./grouping";
import { ProjectHeader } from "./ProjectHeader";
import { projectQuery, projectTasksQuery } from "./queries";
import { ContextSheet } from "./rail/ContextSheet";
import { RightRail } from "./rail/RightRail";
import { TasksView } from "./TasksView";
import { ViewSwitcher, viewTabId } from "./ViewSwitcher";

export interface ProjectPageProps {
  projectId: string;
  view: ProjectView | undefined;
  taskId: string | undefined;
  /** The Calendar view's ISO Monday; undefined: the current week. */
  week?: string | undefined;
  onWeek?: (monday: string) => void;
  onView: (view: ProjectView) => void;
  onTask: (taskId: string | undefined) => void;
}

/** On a laptop the view sits in the tabpanel its tabs control; the phone's radio group
 * needs none. */
function ViewPanel({
  laptop,
  panelId,
  view,
  children,
}: {
  laptop: boolean;
  panelId: string;
  view: ProjectView;
  children: ReactNode;
}) {
  if (!laptop) return <>{children}</>;
  return (
    <div
      role="tabpanel"
      id={panelId}
      aria-labelledby={viewTabId(panelId, view)}
      className="min-w-0"
    >
      {children}
    </div>
  );
}

export function ProjectPage({
  projectId,
  view: searchView,
  taskId,
  week,
  onWeek = () => undefined,
  onView,
  onTask,
}: ProjectPageProps) {
  const laptop = useIsLaptop();
  const panelId = useId();
  const lastView = useSelector(uiStore, (s) => s.context.lastView[projectId]);
  const view = searchView ?? lastView ?? "tasks";
  const project = useQuery(projectQuery(projectId));
  const tasks = useQuery(projectTasksQuery(projectId));
  const workspace = useQuery(workspaceQuery());
  const timezone = workspace.data?.timezone ?? "UTC";
  const [now] = useState(() => new Date());

  if (project.isPending) {
    return <p className="text-muted">Loading the project…</p>;
  }
  if (project.isError) {
    return (
      <p role="alert" className="text-danger">
        This project could not be loaded.
      </p>
    );
  }
  const items = tasks.data?.items ?? [];
  const today = items.filter((t) => groupOf(t, now, timezone) === "today");
  const changeView = (next: ProjectView) => {
    uiStore.trigger.setLastView({ projectId, view: next });
    onView(next);
  };

  return (
    <div className="flex gap-8">
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <ProjectHeader project={project.data} today={today} />
        {!laptop && (
          <ContextSheet
            project={project.data}
            onOpenTask={(id) => {
              onTask(id);
            }}
          />
        )}
        <Composer projectId={projectId} />
        <ViewSwitcher
          view={view}
          laptop={laptop}
          panelId={panelId}
          onChange={changeView}
        />
        <ViewPanel laptop={laptop} panelId={panelId} view={view}>
          {view === "calendar" ? (
            <CalendarView
              projectId={projectId}
              week={week ?? mondayOf(now, timezone)}
              onWeek={onWeek}
            />
          ) : view === "board" ? (
            <BoardView
              projectId={projectId}
              onOpen={(id) => {
                onTask(id);
              }}
            />
          ) : tasks.isError ? (
            <p role="alert" className="text-danger">
              The tasks could not be loaded.
            </p>
          ) : (
            <TasksView
              tasks={items}
              now={now}
              timezone={timezone}
              onOpen={(id) => {
                onTask(id);
              }}
            />
          )}
        </ViewPanel>
      </div>
      {laptop && (
        <RightRail
          project={project.data}
          onOpenTask={(id) => {
            onTask(id);
          }}
        />
      )}
      {taskId && (
        <TaskDrawer
          taskId={taskId}
          laptop={laptop}
          onClose={() => {
            onTask(undefined);
          }}
        />
      )}
    </div>
  );
}

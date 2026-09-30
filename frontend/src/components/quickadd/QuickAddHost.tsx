// The quick-add host (P0-25, FR-3.3, FR-3.9, FR-3.10), in the app shell: the app's one
// offline-queue actor, told ONLINE and OFFLINE by the window's events, by the page
// becoming visible again (an iPhone waking the PWA) and by signing back in (a 401 leaves
// its item waiting); the `/` and Mod+K shortcuts; the quick-add dialog, the search
// palette and the phone's Quick add button; and the one place a capture the server
// refused (a conflict) is retried, edited or discarded. `<body data-offline-queue-state>`
// mirrors the machine's state for the end-to-end tests.
import { useQueryClient } from "@tanstack/react-query";
import { useRouterState } from "@tanstack/react-router";
import { useActorRef, useSelector as useActorSelector } from "@xstate/react";
import { useSelector } from "@xstate/store-react";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import type { ProjectOut } from "../../api/types.gen";
import { useHotkeys } from "../../lib/hotkeys";
import { setSenderHooks } from "../../lib/queueSender";
import { PUBLIC_PATHS } from "../../lib/session";
import { invalidateTaskViews } from "../../lib/task-cache";
import { remember } from "../../lib/undo";
import { offlineQueueMachine } from "../../machines/offlineQueue";
import { uiStore } from "../../stores/uiStore";
import { SearchPalette } from "../common/SearchPalette";
import { QuickAddFab } from "./QuickAddFab";
import { projectQuery } from "../project/queries";
import { QuickAddDialog } from "./QuickAddDialog";
import type { ProjectChoice } from "./ProjectTypeahead";
import { type QueueActor, QueueContext } from "./queue";

const ACTION = "min-h-11 rounded px-2 font-medium text-accent md:min-h-8";
const PROJECT_PATH = /^\/projects\/([0-9a-f-]{36})(?:\/|$)/;

function stateName(actor: QueueActor): string {
  const value = actor.getSnapshot().value;
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** A capture the server refused: what it said, and Retry, Edit or Discard. */
function ConflictPanel({ actor }: { actor: QueueActor }) {
  const head = useActorSelector(actor, (s) =>
    s.matches("conflict") ? s.context.items[0] : undefined,
  );
  const [editing, setEditing] = useState<string | null>(null);
  if (!head) return null;
  const reason =
    head.problem?.detail ?? head.problem?.title ?? "The server refused it.";
  return (
    <div className="fixed inset-x-0 bottom-[calc(4.5rem+env(safe-area-inset-bottom))] z-40 flex justify-center px-4 md:bottom-4">
      <div
        role="alert"
        className="flex w-full max-w-md flex-col gap-2 rounded-md border border-border bg-surface px-4 py-3 text-sm shadow-lg"
      >
        <p>
          <span className="font-medium">“{head.body.title}”</span> was not
          saved: {reason}
        </p>
        {editing === null ? (
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => {
                actor.send({ type: "RETRY" });
              }}
              className={ACTION}
            >
              Retry
            </button>
            <button
              type="button"
              onClick={() => {
                setEditing(head.body.title);
              }}
              className={ACTION}
            >
              Edit
            </button>
            <button
              type="button"
              onClick={() => {
                actor.send({ type: "DISCARD" });
              }}
              className={ACTION}
            >
              Discard
            </button>
          </div>
        ) : (
          <form
            className="flex gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              const title = editing.trim();
              if (title === "") return;
              actor.send({ type: "EDIT", body: { ...head.body, title } });
              setEditing(null);
            }}
          >
            <input
              type="text"
              aria-label="Title"
              value={editing}
              maxLength={500}
              onChange={(event) => {
                setEditing(event.target.value);
              }}
              className="min-h-11 flex-1 rounded-md border border-border bg-bg px-2 md:min-h-8"
            />
            <button type="submit" className={ACTION}>
              Save
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

export function QuickAddHost({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const bare = PUBLIC_PATHS.has(pathname);
  const actor = useActorRef(
    offlineQueueMachine.provide({
      actions: {
        announceSynced: () => {
          void invalidateTaskViews(queryClient);
        },
      },
    }),
    { input: { online: navigator.onLine } },
  );
  const quickAddOpen = useSelector(uiStore, (s) => s.context.quickAddOpen);
  const searchOpen = useSelector(uiStore, (s) => s.context.searchOpen);
  const status = useActorSelector(actor, (s) =>
    s.matches("syncing")
      ? "Syncing…"
      : s.matches("done")
        ? "Synced"
        : s.context.items.length > 0 && !s.context.online
          ? `${String(s.context.items.length)} waiting to sync`
          : "",
  );

  // Sent tasks refresh every task view before their pending rows go, and can be undone.
  useEffect(() => {
    setSenderHooks({
      queryClient,
      onCreated: (task) => {
        remember(task, "Task added");
      },
    });
    return () => {
      setSenderHooks({});
    };
  }, [queryClient]);

  useEffect(() => {
    const body = document.body;
    body.dataset.offlineQueueState = stateName(actor);
    const subscription = actor.subscribe(() => {
      body.dataset.offlineQueueState = stateName(actor);
    });
    return () => {
      subscription.unsubscribe();
      delete body.dataset.offlineQueueState;
    };
  }, [actor]);

  useEffect(() => {
    const online = () => {
      actor.send({ type: "ONLINE" });
      // Reads that failed while offline try again.
      void queryClient.invalidateQueries({
        predicate: (query) => query.state.status === "error",
      });
    };
    const offline = () => {
      actor.send({ type: "OFFLINE" });
    };
    const visible = () => {
      if (document.visibilityState !== "visible") return;
      actor.send({ type: navigator.onLine ? "ONLINE" : "OFFLINE" });
    };
    window.addEventListener("online", online);
    window.addEventListener("offline", offline);
    document.addEventListener("visibilitychange", visible);
    return () => {
      window.removeEventListener("online", online);
      window.removeEventListener("offline", offline);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [actor, queryClient]);

  // The dialogs belong to the host: they do not outlive it.
  useEffect(
    () => () => {
      uiStore.trigger.closeQuickAdd();
      uiStore.trigger.toggleSearch({ open: false });
    },
    [],
  );

  // Back from /login: an item held by a 401 goes now.
  const wasBare = useRef(bare);
  useEffect(() => {
    if (wasBare.current && !bare && navigator.onLine) {
      actor.send({ type: "ONLINE" });
    }
    wasBare.current = bare;
  }, [actor, bare]);

  useHotkeys(
    {
      onQuickAdd: useCallback(() => {
        uiStore.trigger.toggleSearch({ open: false });
        uiStore.trigger.openQuickAdd();
      }, []),
      onSearch: useCallback(() => {
        uiStore.trigger.closeQuickAdd();
        uiStore.trigger.toggleSearch({});
      }, []),
    },
    !bare,
  );

  // On a project page quick add starts with that project.
  const projectId = PROJECT_PATH.exec(pathname)?.[1];
  const current = projectId
    ? queryClient.getQueryData<ProjectOut>(projectQuery(projectId).queryKey)
    : undefined;
  const defaultProject: ProjectChoice | null = current
    ? { id: current.id, name: current.name }
    : null;

  return (
    <QueueContext.Provider value={actor}>
      {children}
      {!bare && <QuickAddFab />}
      {!bare && quickAddOpen && (
        <QuickAddDialog
          defaultProject={defaultProject}
          onClose={() => {
            uiStore.trigger.closeQuickAdd();
          }}
        />
      )}
      {!bare && searchOpen && (
        <SearchPalette
          onClose={() => {
            uiStore.trigger.toggleSearch({ open: false });
          }}
        />
      )}
      <ConflictPanel actor={actor} />
      <p aria-live="polite" className="sr-only">
        {status}
      </p>
    </QueueContext.Provider>
  );
}

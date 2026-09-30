// The projects this device has seen (P0-25, FR-3.10): id and name, kept in localStorage as
// the project list and single projects load, so quick add can still pick a project when
// the device is offline and the page it opened on holds none. A 401 forgets them.
import type { Query, QueryClient } from "@tanstack/react-query";

import { safeGetItem, safeSetItem } from "./storage";
import { queryId } from "./task-cache";

export const KNOWN_PROJECTS_KEY = "tumnis.knownProjects";

export interface KnownProject {
  id: string;
  name: string;
}

const MAX = 200;

function isKnown(value: unknown): value is KnownProject {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as KnownProject).id === "string" &&
    typeof (value as KnownProject).name === "string"
  );
}

/** The stored projects; unreadable JSON and rows of another shape read as none. */
export function loadKnownProjects(): KnownProject[] {
  try {
    const raw: unknown = JSON.parse(safeGetItem(KNOWN_PROJECTS_KEY) ?? "[]");
    return Array.isArray(raw)
      ? raw.filter(isKnown).map(({ id, name }) => ({ id, name }))
      : [];
  } catch {
    return [];
  }
}

function store(projects: KnownProject[]): void {
  safeSetItem(KNOWN_PROJECTS_KEY, JSON.stringify(projects.slice(0, MAX)));
}

/** Keeps `projects` (newest first), replacing a stored project of the same id. */
export function rememberProjects(projects: readonly KnownProject[]): void {
  const fresh = new Map(
    projects.map((p) => [p.id, { id: p.id, name: p.name }]),
  );
  const kept = loadKnownProjects().filter((p) => !fresh.has(p.id));
  store([...fresh.values(), ...kept]);
}

export function forgetProjects(): void {
  store([]);
}

/** Remembers every project a successful project read returns. */
export function trackKnownProjects(client: QueryClient): void {
  client.getQueryCache().subscribe((event) => {
    if (event.type !== "updated" || event.action.type !== "success") return;
    const query = event.query as Query;
    const id = queryId(query.queryKey);
    if (id !== "projectsListProjects" && id !== "projectsGetProject") return;
    const data: unknown = query.state.data;
    const rows: unknown[] =
      typeof data === "object" && data !== null && "items" in data
        ? (data as { items: unknown[] }).items
        : [data];
    rememberProjects(rows.filter(isKnown));
  });
}

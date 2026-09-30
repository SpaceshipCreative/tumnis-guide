// The project picker in quick add (P0-25, FR-3.9): a combobox over
// `GET /v1/typeahead/projects?q=` (debounced), followed by the projects this page already
// holds that match (the project list is read when the picker mounts), so a project just
// created (before the search index has it) and capture while offline still find their
// project. Arrow keys move, Enter picks, Escape
// closes the list.
import {
  type QueryClient,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { type KeyboardEvent, type RefObject, useId, useState } from "react";

import { searchTypeaheadProjectsOptions } from "../../api/@tanstack/react-query.gen";
import { useDebounced } from "../../lib/debounce";
import { loadKnownProjects } from "../../lib/knownProjects";
import { queryId } from "../../lib/task-cache";
import { projectsQuery } from "../dashboard/queries";

export interface ProjectChoice {
  id: string;
  name: string;
}

const LIMIT = 8;

interface ProjectLike {
  id: string;
  name: string;
}

function isProject(value: unknown): value is ProjectLike {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as ProjectLike).id === "string" &&
    typeof (value as ProjectLike).name === "string"
  );
}

/** Projects in the Query cache (the project list, single projects) and those this device saw earlier, matching `q`. */
export function cachedProjects(
  client: QueryClient,
  q: string,
): ProjectChoice[] {
  const needle = q.trim().toLowerCase();
  if (needle === "") return [];
  const found = new Map<string, ProjectChoice>();
  const entries = client.getQueriesData({
    predicate: (query) =>
      ["projectsListProjects", "projectsGetProject"].includes(
        queryId(query.queryKey) ?? "",
      ),
  });
  for (const [, data] of entries) {
    const rows: unknown[] =
      typeof data === "object" && data !== null && "items" in data
        ? (data as { items: unknown[] }).items
        : [data];
    for (const row of rows) {
      if (isProject(row) && row.name.toLowerCase().includes(needle)) {
        found.set(row.id, { id: row.id, name: row.name });
      }
    }
  }
  // Projects this device saw on earlier pages, for a page that holds none (offline).
  for (const known of loadKnownProjects()) {
    if (known.name.toLowerCase().includes(needle)) found.set(known.id, known);
  }
  return [...found.values()];
}

export function ProjectTypeahead({
  value,
  onChange,
  inputRef,
  invalid,
  describedBy,
}: {
  value: ProjectChoice | null;
  onChange: (project: ProjectChoice | null) => void;
  inputRef: RefObject<HTMLInputElement | null>;
  invalid: boolean;
  describedBy?: string | undefined;
}) {
  const client = useQueryClient();
  const listId = useId();
  const [text, setText] = useState(value?.name ?? "");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const q = useDebounced(text.trim());
  const typing = open && q !== "" && q !== value?.name;
  const hits = useQuery({
    ...searchTypeaheadProjectsOptions({ query: { q, limit: LIMIT } }),
    enabled: typing,
    retry: false,
  });
  // The project list (the dashboard's query, so usually already cached) is what the local
  // matches come from; loading it here keeps a project findable on a route that has not
  // read it yet, and warms the cache for capture while offline.
  useQuery({ ...projectsQuery(), retry: false });
  const options: ProjectChoice[] = [];
  const seen = new Set<string>();
  const server = typing
    ? (hits.data ?? []).map((h) => ({ id: h.entity_id, name: h.title }))
    : [];
  for (const choice of [
    ...server,
    ...(typing ? cachedProjects(client, q) : []),
  ]) {
    if (!seen.has(choice.id)) {
      seen.add(choice.id);
      options.push(choice);
    }
  }
  const shown = options.slice(0, LIMIT);
  const expanded = typing && shown.length > 0;
  const optionId = (i: number) => `${listId}-option-${String(i)}`;

  const pick = (choice: ProjectChoice) => {
    onChange(choice);
    setText(choice.name);
    setOpen(false);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!expanded) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((i) => (i + step + shown.length) % shown.length);
    } else if (event.key === "Enter") {
      const choice = shown[active];
      if (choice) {
        event.preventDefault();
        pick(choice);
      }
    } else if (event.key === "Escape") {
      // Close the list, not the dialog.
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
    }
  };

  return (
    <div className="relative">
      <input
        ref={inputRef}
        type="text"
        role="combobox"
        aria-label="Project"
        aria-autocomplete="list"
        aria-expanded={expanded}
        aria-controls={listId}
        aria-activedescendant={expanded ? optionId(active) : undefined}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        autoComplete="off"
        placeholder="Project"
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          setOpen(true);
          setActive(0);
          if (value) onChange(null);
        }}
        onKeyDown={onKeyDown}
        onBlur={() => {
          setOpen(false);
        }}
        className="min-h-11 w-full rounded-md border border-border bg-surface px-3 text-base md:min-h-9 md:text-sm"
      />
      <ul
        id={listId}
        role="listbox"
        aria-label="Projects"
        hidden={!expanded}
        className="absolute inset-x-0 top-full z-10 mt-1 max-h-64 overflow-auto rounded-md border border-border bg-surface py-1 shadow-lg"
      >
        {shown.map((choice, i) => (
          <li
            key={choice.id}
            id={optionId(i)}
            role="option"
            aria-selected={i === active}
            // Keep focus in the input so the list does not close before the click.
            onMouseDown={(event) => {
              event.preventDefault();
            }}
            onClick={() => {
              pick(choice);
            }}
            className="cursor-pointer px-3 py-2 text-sm aria-selected:bg-surface-muted"
          >
            {choice.name}
          </li>
        ))}
      </ul>
    </div>
  );
}

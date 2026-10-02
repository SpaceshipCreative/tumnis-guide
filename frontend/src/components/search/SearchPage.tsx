// The Search page (P0-22's route and search schema, FR-3.9; APP-02): a search box bound to
// `?q=`, the scope (`?scope=` all, tasks or projects) and the results of `GET /v1/search`,
// best first, a page at a time ("Load more"). A task result opens that task on its project
// page, a project result the project, as in the Mod+K palette (SearchPalette).
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { searchSearchInfiniteOptions } from "../../api/@tanstack/react-query.gen";
import type { SearchHit } from "../../api/types.gen";
import { useDebounced } from "../../lib/debounce";
import { Card } from "../common/Card";
import { BUTTON_SECONDARY, FIELD, HINT } from "../common/ui";

export type SearchScope = "all" | "tasks" | "projects";

const SCOPES: readonly { value: SearchScope; text: string }[] = [
  { value: "all", text: "All" },
  { value: "tasks", text: "Tasks" },
  { value: "projects", text: "Projects" },
];
const PAGE_SIZE = 20;

function Hit({ hit }: { hit: SearchHit }) {
  const body = (
    <>
      <span className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 font-medium break-words">{hit.title}</span>
        <span className="shrink-0 text-xs text-muted">
          {hit.entity_type === "task" ? "Task" : "Project"}
        </span>
      </span>
      {hit.snippet !== "" && hit.snippet !== hit.title && (
        <span className="block text-sm break-words text-muted">
          {hit.snippet}
        </span>
      )}
    </>
  );
  const className =
    "block rounded-md px-2 py-2 hover:bg-surface-muted focus-visible:bg-surface-muted";
  if (hit.entity_type === "project" || hit.project_id === null) {
    return (
      <Link
        to="/projects/$projectId"
        params={{ projectId: hit.project_id ?? hit.entity_id }}
        className={className}
      >
        {body}
      </Link>
    );
  }
  return (
    <Link
      to="/projects/$projectId"
      params={{ projectId: hit.project_id }}
      search={{ task: hit.entity_id }}
      className={className}
    >
      {body}
    </Link>
  );
}

function Results({ q, scope }: { q: string; scope: SearchScope }) {
  const results = useInfiniteQuery({
    ...searchSearchInfiniteOptions({
      query: { q, scope, limit: PAGE_SIZE },
    }),
    // An object page param merges into the request; later pages pass the cursor.
    initialPageParam: {},
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    retry: false,
  });
  if (results.isPending) {
    return <p className={HINT}>Searching…</p>;
  }
  // A failed first page has nothing to show; a failed later page keeps the pages already
  // loaded and offers to try that page again.
  if (results.data === undefined) {
    return (
      <p role="alert" className="text-sm text-danger">
        Search is not available right now.
      </p>
    );
  }
  const hits = results.data.pages.flatMap((page) => page.items);
  return (
    <Card title="Results" bodyClassName="px-2 py-1">
      {hits.length === 0 ? (
        <p className={`${HINT} px-2 py-2`}>Nothing found</p>
      ) : (
        <ul
          aria-label="Results"
          className="flex flex-col divide-y divide-border"
        >
          {hits.map((hit) => (
            <li key={`${hit.entity_type}:${hit.entity_id}`}>
              <Hit hit={hit} />
            </li>
          ))}
        </ul>
      )}
      {results.isFetchNextPageError && !results.isFetchingNextPage ? (
        <p
          role="alert"
          className="mx-2 my-2 flex items-center gap-3 text-sm text-danger"
        >
          More results could not be loaded.
          <button
            type="button"
            className={`${BUTTON_SECONDARY} px-3`}
            onClick={() => {
              void results.fetchNextPage();
            }}
          >
            Try again
          </button>
        </p>
      ) : (
        results.hasNextPage && (
          <button
            type="button"
            className={`${BUTTON_SECONDARY} my-2 ml-2 px-3`}
            disabled={results.isFetchingNextPage}
            onClick={() => {
              void results.fetchNextPage();
            }}
          >
            {results.isFetchingNextPage ? "Loading…" : "Load more"}
          </button>
        )
      )}
    </Card>
  );
}

export function SearchPage({
  q,
  scope,
  onSearch,
}: {
  q: string;
  scope: SearchScope;
  /** A new query or scope, for the URL (`q` trimmed, after a pause in typing). */
  onSearch: (next: { q: string; scope: SearchScope }) => void;
}) {
  const boxId = useId();
  // The box's own text belongs to the `q` it was typed against (`for`): when the URL's `q`
  // changes from outside (back, forward, a link), the box shows the new `q` instead. A
  // search the box asks for itself rebinds the text to the `q` it asks for at once, and
  // `from` keeps the `q` the URL still holds until that navigation lands, so the box's own
  // URL write never takes back what is being typed (a trailing space, or keys pressed
  // before the URL changed).
  const [draft, setDraft] = useState({ for: q, from: q, text: q });
  const own = draft.for === q || draft.from === q;
  if (draft.for === q && draft.from !== q) setDraft({ ...draft, from: q }); // it landed
  const text = own ? draft.text : q;
  const setText = (next: string) => {
    setDraft(own ? { ...draft, text: next } : { for: q, from: q, text: next });
  };
  const search = useCallback(
    (next: string, nextScope: SearchScope) => {
      setDraft((d) => ({
        for: next,
        from: q,
        text: d.for === q || d.from === q ? d.text : q,
      }));
      onSearch({ q: next, scope: nextScope });
    },
    [q, onSearch],
  );
  const settled = useDebounced(text.trim());
  // Only a change of the settled text searches, so text left over from before an outside
  // change never puts the old search back in the URL.
  const lastSettled = useRef(settled);
  useEffect(() => {
    if (settled === lastSettled.current) return;
    lastSettled.current = settled;
    if (settled !== q) search(settled, scope);
  }, [settled, q, scope, search]);

  return (
    <section className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">Search</h1>
      <form
        role="search"
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (text.trim() !== q) search(text.trim(), scope);
        }}
      >
        <label htmlFor={boxId} className="sr-only">
          Search tasks and projects
        </label>
        <input
          id={boxId}
          type="search"
          placeholder="Search tasks and projects"
          maxLength={200}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
          }}
          className={`${FIELD} w-full`}
        />
        <fieldset className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <legend className="sr-only">Search in</legend>
          {SCOPES.map((option) => (
            <label
              key={option.value}
              className="flex min-h-11 items-center gap-2 text-sm md:min-h-8"
            >
              <input
                type="radio"
                name="scope"
                value={option.value}
                checked={scope === option.value}
                onChange={() => {
                  search(text.trim(), option.value);
                }}
              />
              {option.text}
            </label>
          ))}
        </fieldset>
      </form>
      {q === "" ? (
        <p className={HINT}>
          Type to search task titles, first actions, comments and projects.
        </p>
      ) : (
        <Results q={q} scope={scope} />
      )}
    </section>
  );
}

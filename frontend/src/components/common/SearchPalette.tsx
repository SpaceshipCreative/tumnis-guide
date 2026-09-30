// Global search (P0-25, FR-3.9): Mod+K on any screen opens a palette over
// `GET /v1/search?q=` (debounced). A task result opens that task on its project page; a
// project result opens the project. Escape (or Mod+K again) closes it.
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { searchSearchOptions } from "../../api/@tanstack/react-query.gen";
import type { SearchHit } from "../../api/types.gen";
import { useDebounced } from "../../lib/debounce";

const LIMIT = 10;

function Result({ hit, onPick }: { hit: SearchHit; onPick: () => void }) {
  const kind = hit.entity_type === "task" ? "Task" : "Project";
  const body = (
    <>
      <span className="block truncate font-medium">{hit.title}</span>
      <span className="text-xs text-muted">{kind}</span>
    </>
  );
  const className =
    "block rounded-md px-3 py-2 hover:bg-surface-muted focus-visible:bg-surface-muted";
  if (hit.entity_type === "project" || hit.project_id === null) {
    return (
      <Link
        to="/projects/$projectId"
        params={{ projectId: hit.project_id ?? hit.entity_id }}
        onClick={onPick}
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
      onClick={onPick}
      className={className}
    >
      {body}
    </Link>
  );
}

export function SearchPalette({ onClose }: { onClose: () => void }) {
  const [text, setText] = useState("");
  const boxRef = useRef<HTMLInputElement>(null);
  const q = useDebounced(text.trim());
  const results = useQuery({
    ...searchSearchOptions({ query: { q, limit: LIMIT } }),
    enabled: q !== "",
    retry: false,
  });

  useEffect(() => {
    const opener =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    boxRef.current?.focus();
    return () => {
      opener?.focus();
    };
  }, []);

  const hits = q === "" ? [] : (results.data?.items ?? []);
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 px-4 pt-[15vh]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search"
        className="w-full max-w-lg rounded-lg border border-border bg-surface p-3 shadow-xl"
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            onClose();
          }
        }}
      >
        <input
          ref={boxRef}
          type="search"
          aria-label="Search"
          placeholder="Search tasks and projects"
          value={text}
          onChange={(event) => {
            setText(event.target.value);
          }}
          className="min-h-11 w-full rounded-md border border-border bg-bg px-3 text-base md:min-h-9 md:text-sm"
        />
        {q !== "" && (
          <div className="mt-2 max-h-[50vh] overflow-auto">
            {results.isError ? (
              <p className="px-3 py-2 text-sm text-danger">
                Search is not available right now.
              </p>
            ) : hits.length === 0 ? (
              <p className="px-3 py-2 text-sm text-muted">
                {results.isFetching ? "Searching…" : "Nothing found"}
              </p>
            ) : (
              <ul aria-label="Results" className="flex flex-col">
                {hits.map((hit) => (
                  <li key={`${hit.entity_type}:${hit.entity_id}`}>
                    <Result hit={hit} onPick={onClose} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

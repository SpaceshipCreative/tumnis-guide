// Search (P0-22 route and search schema; the screen, FR-3.9: APP-02). `q` and `scope` live
// in the URL (replaced, not pushed, as the search changes), so a search can be linked
// and reloaded.
import { createFileRoute } from "@tanstack/react-router";
import { useCallback } from "react";
import * as z from "zod";

import { SearchPage, type SearchScope } from "../components/search/SearchPage";

export const searchSearch = z.object({
  q: z.string().max(200).catch(""),
  scope: z.enum(["all", "tasks", "projects"]).catch("all"),
});

export const Route = createFileRoute("/search")({
  validateSearch: searchSearch,
  component: SearchRoute,
});

function SearchRoute() {
  const { q, scope } = Route.useSearch();
  const navigate = Route.useNavigate();
  const onSearch = useCallback(
    (next: { q: string; scope: SearchScope }) => {
      void navigate({ search: next, replace: true });
    },
    [navigate],
  );
  return <SearchPage q={q} scope={scope} onSearch={onSearch} />;
}

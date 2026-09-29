// Search (P0-22 route; the screen arrives with P0-24).
import { createFileRoute } from "@tanstack/react-router";
import * as z from "zod";

import { Placeholder } from "../components/pages/Placeholder";

export const searchSearch = z.object({
  q: z.string().max(200).catch(""),
  scope: z.enum(["all", "tasks", "projects"]).catch("all"),
});

export const Route = createFileRoute("/search")({
  validateSearch: searchSearch,
  component: SearchPage,
});

function SearchPage() {
  return <Placeholder title="Search" />;
}

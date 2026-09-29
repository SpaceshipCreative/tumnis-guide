// The dashboard (P0-22 route; the screen and its loader arrive with P0-23).
import { createFileRoute } from "@tanstack/react-router";
import * as z from "zod";

import { Placeholder } from "../components/pages/Placeholder";

export const dashboardSearch = z.object({
  focus: z
    .enum(["quiet", "nudge", "coach", "guardrail"])
    .optional()
    .catch(undefined),
});

export const Route = createFileRoute("/")({
  validateSearch: dashboardSearch,
  component: DashboardPage,
});

function DashboardPage() {
  return <Placeholder title="Dashboard" />;
}

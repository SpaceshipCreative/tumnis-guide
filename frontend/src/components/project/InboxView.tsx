// The project's Inbox (P2-17, FR-2.6): the tasks the agent proposes from this project's
// email, chat and notes, waiting for a decision. P3-07 fills it; until then it explains
// what will arrive here. Deciding stays in the review queue.
import { useQuery } from "@tanstack/react-query";

import { tasksListInboxOptions } from "../../api/@tanstack/react-query.gen";
import { Card } from "../common/Card";

export const inboxQuery = (projectId: string) =>
  tasksListInboxOptions({ path: { project_id: projectId }, query: { limit: 50 } });

export function InboxView({ projectId }: { projectId: string }) {
  const inbox = useQuery(inboxQuery(projectId));
  if (inbox.isPending) return <p className="text-muted">Loading the inbox…</p>;
  if (inbox.isError) {
    return (
      <p role="alert" className="text-danger">
        The inbox could not be loaded.
      </p>
    );
  }
  const items = inbox.data.items;
  if (items.length === 0) {
    return (
      <Card title="No proposals yet" bodyClassName="px-4 py-3">
        <p className="text-sm text-muted">
          Tasks the agent suggests from this project's email, chat and notes
          wait here.
        </p>
      </Card>
    );
  }
  return (
    <Card title="Proposals" bodyClassName="px-4 py-1">
      <ul aria-label="Inbox" className="flex flex-col divide-y divide-border">
        {items.map((item) => (
          <li key={item.id} className="py-2 text-sm">
            {item.target_title ?? "Untitled proposal"}
          </li>
        ))}
      </ul>
    </Card>
  );
}

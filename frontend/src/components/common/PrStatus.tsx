// The pull request chip (P2-13, FR-12.1). Spec seam: the chip lands with the WP's
// implementation.
export interface PullRequest {
  artifact_id: string;
  url: string;
  repo: string;
  number: number;
  title: string | null;
  state: "open" | "merged" | "closed" | null;
  draft: boolean;
  checks: "pending" | "green" | "red" | "none" | null;
  review: "approved" | "changes_requested" | "review_required" | "none" | null;
  checked_at: string | null;
}

export function PrStatus({ pr }: { pr: PullRequest }) {
  return pr.url ? null : null;
}

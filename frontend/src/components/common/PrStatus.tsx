// The pull request chip (P2-13, FR-12.1): state, combined checks and review, in words and
// in the link's accessible name, so a red build is not told by colour alone.
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

// [visible text, spoken text]
const STATE_WORDS = {
  open: ["Open", "open"],
  merged: ["Merged", "merged"],
  closed: ["Closed", "closed"],
  draft: ["Draft", "draft"],
} as const;
const CHECK_WORDS = {
  green: ["Checks passing", "checks passing"],
  red: ["Checks failing", "checks failing"],
  pending: ["Checks running", "checks running"],
  none: ["No checks", "no checks"],
} as const;
const REVIEW_WORDS = {
  approved: ["Approved", "approved"],
  changes_requested: ["Changes requested", "changes requested"],
  review_required: ["Review required", "review required"],
  none: ["No review", "no review"],
} as const;

const CHIP =
  "rounded-full border border-border px-2 py-0.5 text-xs whitespace-nowrap";

export function PrStatus({ pr }: { pr: PullRequest }) {
  const ref = `${pr.repo}#${String(pr.number)}`;
  const state =
    pr.state === null
      ? null
      : STATE_WORDS[pr.draft && pr.state === "open" ? "draft" : pr.state];
  const checks = pr.checks === null ? null : CHECK_WORDS[pr.checks];
  const review = pr.review === null ? null : REVIEW_WORDS[pr.review];
  const words = state && checks && review ? { state, checks, review } : null;
  const label = words
    ? `Pull request ${ref}, ${pr.title ?? "untitled"}: ${words.state[1]}, ${words.checks[1]}, ${words.review[1]}`
    : `Pull request ${ref}${pr.title ? `, ${pr.title}` : ""}: status not read yet`;
  return (
    <span
      data-checks={pr.checks ?? "unknown"}
      className="inline-flex max-w-full items-center"
    >
      <a
        href={pr.url}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={label}
        className="inline-flex min-h-11 max-w-full flex-wrap items-center gap-1 rounded-md md:min-h-6"
      >
        <span className="text-sm font-medium text-accent">
          {`#${String(pr.number)}`}
        </span>
        {words ? (
          <>
            <span className={CHIP}>{words.state[0]}</span>
            <span
              className={`${CHIP} ${pr.checks === "red" ? "border-danger font-semibold text-danger" : ""}`}
            >
              {words.checks[0]}
            </span>
            <span className={CHIP}>{words.review[0]}</span>
          </>
        ) : (
          <span className={`${CHIP} text-muted`}>Checking…</span>
        )}
      </a>
    </span>
  );
}

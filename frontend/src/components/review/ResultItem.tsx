// A `result` review item (P2-04, FR-5.8): what the agent did in its run (the summary,
// the files it touched, its links) and the human's answer. Accept finishes the task and
// is one keystroke (Enter on the focused card); Reject sends the task back to the agent
// with the feedback as a comment, so it stays disabled until feedback is typed (r jumps
// to the field). In the review queue (`confirmReject`) Reject first opens the feedback
// field and "Confirm reject" sends it, so the card stays short until it is needed. A
// card like the queue's others (DS-01, ADR-0012), with 44 px controls (UX 11).
import {
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type Ref,
} from "react";

import type { ReviewItemOut } from "../../api/types.gen";
import {
  badge,
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  CARD,
  CARD_BODY,
  CARD_HEADER,
  CARD_TITLE,
  FIELD,
  FIELD_LABEL,
  HINT,
} from "../common/ui";

export type ResultDecide = (
  action: "accept" | "reject",
  payload?: Record<string, unknown>,
) => void;

interface FileTouched {
  path: string;
  change: string;
}

interface ResultLink {
  kind: string;
  url: string;
  label: string | null;
}

const OUTCOMES: Record<string, string> = {
  done: "Done",
  partial: "Partly done",
  blocked: "Blocked",
};

function files(payload: Record<string, unknown>): FileTouched[] {
  const raw = payload.files_touched;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((entry: unknown) => {
    const file = entry as Partial<FileTouched> | null;
    return typeof file?.path === "string"
      ? [
          {
            path: file.path,
            change: typeof file.change === "string" ? file.change : "",
          },
        ]
      : [];
  });
}

function links(payload: Record<string, unknown>): ResultLink[] {
  const raw = payload.links;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((entry: unknown) => {
    const link = entry as Partial<ResultLink> | null;
    // Only web links become links: an agent's text never becomes a script URL.
    return typeof link?.url === "string" && /^https?:\/\//.test(link.url)
      ? [
          {
            kind: typeof link.kind === "string" ? link.kind : "url",
            url: link.url,
            label: typeof link.label === "string" ? link.label : null,
          },
        ]
      : [];
  });
}

export interface ResultItemProps {
  item: ReviewItemOut;
  onDecide: ResultDecide;
  busy?: boolean;
  current?: boolean;
  articleRef?: Ref<HTMLElement>;
  onFocus?: () => void;
  /** The queue's snooze (its s key and choices); no Snooze button without it. */
  onSnooze?: () => void;
  /** Shown under the actions: the queue's snooze choices while it snoozes. */
  children?: ReactNode;
  /** Reject opens the feedback field first, and "Confirm reject" sends it. */
  confirmReject?: boolean;
}

export function ResultItem({
  item,
  onDecide,
  busy = false,
  current = false,
  articleRef,
  onFocus,
  onSnooze,
  children,
  confirmReject = false,
}: ResultItemProps) {
  const titleId = useId();
  const feedbackId = useId();
  const [feedback, setFeedback] = useState("");
  const field = useRef<HTMLTextAreaElement>(null);
  const [rejecting, setRejecting] = useState(false);
  const showForm = !confirmReject || rejecting;
  const payload = item.payload;
  const summary = typeof payload.summary === "string" ? payload.summary : "";
  const outcome = OUTCOMES[String(payload.outcome)] ?? "Result";
  const touched = files(payload);
  const shown = links(payload);
  const tests =
    typeof payload.tests_summary === "string" ? payload.tests_summary : null;
  const trimmed = feedback.trim();

  // Enter accepts the focused card; r goes to the feedback (a reject needs it). Both stop
  // here, so a queue around the card does not act on them as well.
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      if (!busy) onDecide("accept");
    } else if (event.key === "r") {
      event.preventDefault();
      event.stopPropagation();
      setRejecting(true);
      field.current?.focus();
    }
  };

  return (
    <article
      ref={articleRef}
      tabIndex={0}
      aria-labelledby={titleId}
      aria-current={current ? "true" : undefined}
      onKeyDown={onKeyDown}
      onFocus={(event) => {
        if (event.target === event.currentTarget) onFocus?.();
      }}
      className={`${CARD} aria-[current=true]:border-accent`}
    >
      <header className={CARD_HEADER}>
        <h2 id={titleId} className={CARD_TITLE}>
          {item.target_title ?? "Result"}
        </h2>
        <span className={badge("accent")}>{outcome}</span>
      </header>
      <div className={CARD_BODY}>
        <p className="text-sm break-words whitespace-pre-line">{summary}</p>
        {touched.length > 0 && (
          <ul aria-label="Files touched" className="flex flex-col gap-1">
            {touched.map((file) => (
              <li key={file.path} className="flex min-w-0 items-center gap-2">
                <code className="min-w-0 truncate text-sm">{file.path}</code>
                <span className={badge("neutral")}>{file.change}</span>
              </li>
            ))}
          </ul>
        )}
        {shown.length > 0 && (
          <ul aria-label="Links" className="flex flex-wrap gap-x-4 gap-y-1">
            {shown.map((link) => (
              <li key={link.url} className="min-w-0">
                <a
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm break-all text-accent underline"
                >
                  {link.label ?? link.url}
                </a>
              </li>
            ))}
          </ul>
        )}
        {tests !== null && <p className={HINT}>Tests: {tests}</p>}

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={BUTTON_PRIMARY}
            disabled={busy}
            onClick={() => {
              onDecide("accept");
            }}
          >
            Accept
          </button>
          {!showForm && (
            <button
              type="button"
              className={BUTTON_SECONDARY}
              disabled={busy}
              onClick={() => {
                setRejecting(true);
              }}
            >
              Reject
            </button>
          )}
          {onSnooze !== undefined && (
            <button
              type="button"
              className={BUTTON_SECONDARY}
              disabled={busy}
              onClick={onSnooze}
            >
              Snooze
            </button>
          )}
        </div>
        {children}
        {showForm && (
          <form
            className="flex flex-col gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (trimmed === "" || busy) return;
              onDecide("reject", { feedback: trimmed });
            }}
          >
            <label htmlFor={feedbackId} className={FIELD_LABEL}>
              Feedback
              <textarea
                ref={field}
                id={feedbackId}
                rows={2}
                value={feedback}
                maxLength={8000}
                onChange={(event) => {
                  setFeedback(event.target.value);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Escape" && confirmReject) {
                    setRejecting(false);
                  }
                }}
                autoFocus={confirmReject}
                className={FIELD}
              />
            </label>
            <div className="flex flex-wrap gap-2">
              <button
                type="submit"
                className={BUTTON_SECONDARY}
                disabled={busy || trimmed === ""}
              >
                {confirmReject ? "Confirm reject" : "Reject"}
              </button>
              {confirmReject && (
                <button
                  type="button"
                  className={BUTTON_SECONDARY}
                  onClick={() => {
                    setRejecting(false);
                  }}
                >
                  Cancel
                </button>
              )}
            </div>
          </form>
        )}
      </div>
    </article>
  );
}

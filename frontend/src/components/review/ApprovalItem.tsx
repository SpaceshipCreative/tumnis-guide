// An `approval` review item (P2-05, FR-5.6, SEC-3): a run asks to take an action the
// project's policy gates (or any action, on a run that read outside content). The card
// says what the agent wants to do, on what, and why it needs approval. Approve and Deny
// both need a reason, which the audit log keeps with the decision; a preset fills the
// reason in one tap, so a decision works one-handed on a phone (44 px controls, UX 11).
// On the focused card, Enter and r go to the reason field (a decision without a reason is
// refused), and stop there, so the queue around the card does not act on them too.
import {
  useCallback,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type Ref,
} from "react";

import type { ReviewItemOut } from "../../api/types.gen";
import { TaintBadge } from "../common/TaintBadge";
import {
  badge,
  BUTTON_DANGER,
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
import { actionText } from "../project/PolicyEditor";

export type ApprovalDecide = (
  action: "approve" | "deny",
  payload: { reason: string },
) => void;

/** One-tap reasons; the field stays editable after a preset fills it. */
export const REASON_PRESETS: readonly string[] = [
  "Expected for this task",
  "Checked the details",
  "Too risky",
  "Not part of this task",
];

const REASON_MAX = 2000; // the server's limit

// Why the server asked (PolicyVerdict.rule), in words.
const RULES: Record<string, string> = {
  tainted_run:
    "This run read outside content, so every action it takes needs approval.",
  gated_by_policy: "The project's policy needs approval for this action.",
  unknown_needs_decision: "Tumnis does not know this action yet.",
  unknown_below_threshold: "Tumnis is not sure this action is safe.",
  unknown_decided_gated: "Tumnis judged this action risky.",
  decisions_unavailable: "Tumnis could not check whether this action is safe.",
};

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

export interface ApprovalItemProps {
  item: ReviewItemOut;
  onDecide: ApprovalDecide;
  busy?: boolean;
  current?: boolean;
  articleRef?: Ref<HTMLElement>;
  onFocus?: () => void;
  /** The queue's snooze (its s key and choices); no Snooze button without it. */
  onSnooze?: () => void;
  /** Shown under the actions: the queue's snooze choices while it snoozes. */
  children?: ReactNode;
}

export function ApprovalItem({
  item,
  onDecide,
  busy = false,
  current = false,
  articleRef,
  onFocus,
  onSnooze,
  children,
}: ApprovalItemProps) {
  const titleId = useId();
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const field = useRef<HTMLInputElement>(null);
  const setCard = useCallback(
    (node: HTMLElement | null) => {
      if (typeof articleRef === "function") articleRef(node);
      else if (articleRef) articleRef.current = node;
    },
    [articleRef],
  );
  const payload = item.payload;
  const action = text(payload.action_class);
  const description = text(payload.description);
  const target = text(payload.target);
  const rule = text(payload.rule);
  const why = rule === null ? null : (RULES[rule] ?? null);
  const trimmed = reason.trim();
  const blocked = busy || trimmed === "";

  const decide = (choice: "approve" | "deny") => {
    if (blocked) return;
    onDecide(choice, { reason: trimmed });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "Enter" || event.key === "r") {
      event.preventDefault();
      event.stopPropagation();
      field.current?.focus();
    }
  };

  return (
    <article
      ref={setCard}
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
          {item.target_title ?? "Approval"}
        </h2>
        <span className={badge("warning")}>Approval</span>
      </header>
      <div className={CARD_BODY}>
        <p className="text-sm font-medium">
          {action === null ? "An action" : actionText(action)}
        </p>
        {description !== null && (
          <p className="text-sm break-words whitespace-pre-line">
            {description}
          </p>
        )}
        {target !== null && (
          <p className={HINT}>
            On <code className="break-all">{target}</code>
          </p>
        )}
        {why !== null && <p className={HINT}>{why}</p>}
        {item.target_tainted && (
          <div>
            <TaintBadge />
          </div>
        )}

        <label htmlFor={reasonId} className={FIELD_LABEL}>
          Reason
          <input
            ref={field}
            id={reasonId}
            type="text"
            value={reason}
            maxLength={REASON_MAX}
            onChange={(event) => {
              setReason(event.target.value);
            }}
            className={FIELD}
          />
        </label>
        <div
          role="group"
          aria-label="Reason presets"
          className="flex flex-wrap gap-2"
        >
          {REASON_PRESETS.map((preset) => (
            <button
              key={preset}
              type="button"
              className={BUTTON_SECONDARY}
              disabled={busy}
              onClick={() => {
                setReason(preset);
              }}
            >
              {preset}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={BUTTON_PRIMARY}
            disabled={blocked}
            onClick={() => {
              decide("approve");
            }}
          >
            Approve
          </button>
          <button
            type="button"
            className={BUTTON_DANGER}
            disabled={blocked}
            onClick={() => {
              decide("deny");
            }}
          >
            Deny
          </button>
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
      </div>
    </article>
  );
}

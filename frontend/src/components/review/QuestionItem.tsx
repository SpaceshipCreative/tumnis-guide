// A `question` review item (P2-05, FR-5.7): a run asks the human and waits, with no
// deadline, until the answer comes. The card shows the question; a question with choices
// is answered with one tap on a choice, any other with a typed answer. The answer goes
// back to the waiting run, which carries on. On the focused card, Enter goes to the answer
// (the first choice, or the field) and stops there, so the queue around the card does not
// act on it too. Controls are 44 px tall (UX 11).
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
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  CARD,
  CARD_BODY,
  CARD_HEADER,
  CARD_TITLE,
  FIELD,
  FIELD_LABEL,
} from "../common/ui";

export type QuestionDecide = (
  action: "answer",
  payload: { answer: string },
) => void;

const ANSWER_MAX = 4000; // the server's limit

function choicesOf(payload: Record<string, unknown>): string[] {
  const raw = payload.choices;
  return Array.isArray(raw)
    ? raw.filter((c): c is string => typeof c === "string" && c !== "")
    : [];
}

export interface QuestionItemProps {
  item: ReviewItemOut;
  onDecide: QuestionDecide;
  busy?: boolean;
  current?: boolean;
  articleRef?: Ref<HTMLElement>;
  onFocus?: () => void;
  /** The queue's snooze (its s key and choices); no Snooze button without it. */
  onSnooze?: () => void;
  /** Shown under the actions: the queue's snooze choices while it snoozes. */
  children?: ReactNode;
}

export function QuestionItem({
  item,
  onDecide,
  busy = false,
  current = false,
  articleRef,
  onFocus,
  onSnooze,
  children,
}: QuestionItemProps) {
  const titleId = useId();
  const answerId = useId();
  const [answer, setAnswer] = useState("");
  const answers = useRef<HTMLDivElement>(null);
  const setCard = useCallback(
    (node: HTMLElement | null) => {
      if (typeof articleRef === "function") articleRef(node);
      else if (articleRef) articleRef.current = node;
    },
    [articleRef],
  );
  const prompt =
    typeof item.payload.prompt === "string" ? item.payload.prompt : "";
  const choices = choicesOf(item.payload);
  const trimmed = answer.trim();

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      answers.current?.querySelector<HTMLElement>("input, button")?.focus();
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
          {item.target_title ?? "Question"}
        </h2>
        <span className={badge("accent")}>Question</span>
      </header>
      <div className={CARD_BODY}>
        <p className="text-sm break-words whitespace-pre-line">{prompt}</p>
        {item.target_tainted && (
          <div>
            <TaintBadge />
          </div>
        )}

        <div ref={answers}>
          {choices.length > 0 ? (
            <div
              role="group"
              aria-label="Choices"
              className="flex flex-wrap gap-2"
            >
              {choices.map((choice) => (
                <button
                  key={choice}
                  type="button"
                  className={BUTTON_SECONDARY}
                  disabled={busy}
                  onClick={() => {
                    onDecide("answer", { answer: choice });
                  }}
                >
                  {choice}
                </button>
              ))}
            </div>
          ) : (
            <form
              className="flex flex-wrap items-end gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                if (trimmed === "" || busy) return;
                onDecide("answer", { answer: trimmed });
              }}
            >
              <label
                htmlFor={answerId}
                className={`${FIELD_LABEL} w-full sm:w-80`}
              >
                Answer
                <input
                  id={answerId}
                  type="text"
                  value={answer}
                  maxLength={ANSWER_MAX}
                  onChange={(event) => {
                    setAnswer(event.target.value);
                  }}
                  className={FIELD}
                />
              </label>
              <button
                type="submit"
                className={BUTTON_PRIMARY}
                disabled={busy || trimmed === ""}
              >
                Answer
              </button>
            </form>
          )}
        </div>
        {onSnooze !== undefined && (
          <div>
            <button
              type="button"
              className={BUTTON_SECONDARY}
              disabled={busy}
              onClick={onSnooze}
            >
              Snooze
            </button>
          </div>
        )}
        {children}
      </div>
    </article>
  );
}

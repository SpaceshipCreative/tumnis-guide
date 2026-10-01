// The return question after a captured detour (P4-01, FR-10.6, FR-10.9): the message
// names the task to go back to, with its rule ("Guardrail · detour"); Return or Stay
// answers it, and it counts as Stay by itself after 2 minutes (the focusSession machine's
// delay), so nothing waits on an answer.
import type { DetourOut } from "../../api/types.gen";
import { BUTTON_PRIMARY, BUTTON_SECONDARY } from "../common/ui";

export function ReturnPrompt({
  detour,
  busy,
  onReturn,
  onStay,
}: {
  detour: DetourOut;
  busy: boolean;
  onReturn: () => void;
  onStay: () => void;
}) {
  return (
    <div
      role="group"
      aria-label="Back to your task?"
      className="flex min-w-0 flex-col gap-2 text-sm"
    >
      <p className="flex min-w-0 flex-col">
        <span className="break-words">{detour.message}</span>
        <span data-testid="focus-attribution" className="text-xs text-muted">
          {detour.rule}
        </span>
      </p>
      <div className="flex flex-wrap gap-2">
        {detour.return_to_task_id !== null && (
          <button
            type="button"
            className={BUTTON_PRIMARY}
            disabled={busy}
            onClick={onReturn}
          >
            Return
          </button>
        )}
        <button
          type="button"
          className={BUTTON_SECONDARY}
          disabled={busy}
          onClick={onStay}
        >
          Stay
        </button>
      </div>
    </div>
  );
}

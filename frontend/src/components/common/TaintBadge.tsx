// The taint mark (P2-08, SAF-1, design decision 14): a task made from outside content (an
// email, a web page, a file someone sent, or anything a run that read them wrote) says so
// in plain words, on its card, in its drawer and on its review items. Tainted work never
// runs unattended and every action on a tainted run needs approval, so the mark tells the
// person why. The tooltip names the source when it is known ("email from
// client@example.com"). A DS-01 warning badge: in words, never colour alone.
import { badge } from "./ui";

export const TAINT_MARK = "From outside content";

export function TaintBadge({ source = null }: { source?: string | null }) {
  const detail = source
    ? `${TAINT_MARK}: ${source}`
    : `${TAINT_MARK}: made from something Tumnis did not write, such as an email or a web page`;
  return (
    <span data-taint-mark className={badge("warning")} title={detail}>
      {TAINT_MARK}
    </span>
  );
}

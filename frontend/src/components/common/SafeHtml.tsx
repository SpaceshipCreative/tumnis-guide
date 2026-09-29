// The one place the app renders HTML (P0-16, SEC-4). The string must come from the API,
// which sanitizes every piece of outside HTML with tumnis.core.sanitize on ingest and again
// on every read; the Semgrep rule tumnis-inner-html forbids dangerouslySetInnerHTML
// anywhere else. Remote images never arrive: the sanitizer drops <img>.
import type { JSX } from "react";

export interface SafeHtmlProps {
  /** Server-sanitized HTML (tumnis.core.sanitize.sanitize_html), never raw input. */
  readonly html: string;
  readonly as?: "div" | "section" | "article";
  readonly className?: string;
}

export function SafeHtml({
  html,
  as: Element = "div",
  className,
}: SafeHtmlProps): JSX.Element {
  return (
    <Element
      {...(className === undefined ? {} : { className })}
      // nosemgrep: typescript.react.security.audit.react-dangerouslysetinnerhtml.react-dangerouslysetinnerhtml
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

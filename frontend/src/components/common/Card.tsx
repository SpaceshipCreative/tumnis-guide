// A card (DS-01, ADR-0012): a labelled region with a header row (its title, and an
// optional action on the right) above its content. The dashboard, project and review
// pages group their content in these.
import { useId, type ReactNode } from "react";

import { CARD, CARD_BODY, CARD_HEADER, CARD_TITLE } from "./ui";

export function Card({
  title,
  headingLevel = 2,
  action,
  children,
  className = "",
  bodyClassName = CARD_BODY,
}: {
  title: string;
  /** The title's heading level, so the card fits the page's outline. */
  headingLevel?: 2 | 3;
  /** A control on the right of the header row, e.g. a link to the full list. */
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  const titleId = useId();
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <section aria-labelledby={titleId} className={`${CARD} ${className}`}>
      <div className={CARD_HEADER}>
        <Heading id={titleId} className={CARD_TITLE}>
          {title}
        </Heading>
        {action}
      </div>
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}

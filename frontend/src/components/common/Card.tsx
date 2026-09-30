// A card (DS-01, ADR-0012): a labelled region with a header row. Stub until the
// implementation lands.
import type { ReactNode } from "react";

export function Card(props: {
  title: string;
  headingLevel?: 2 | 3;
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return <>{props.children}</>;
}

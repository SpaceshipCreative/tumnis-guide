// A screen that arrives with a later work package: its heading and one line.
import type { ReactNode } from "react";

export function Placeholder({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2">
      <h1 className="text-2xl font-semibold">{title}</h1>
      {children}
    </section>
  );
}

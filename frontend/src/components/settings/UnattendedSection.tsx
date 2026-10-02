// Settings > Unattended runs (P4-04, FR-4.5): the workspace's window for AI tasks queued
// to run while you sleep: the nights it opens, a start and an end in the workspace
// timezone (an end before the start runs overnight), Save, and Turn off. A project can set
// its own window from its Schedule rail.
import { useQuery } from "@tanstack/react-query";

import { unattendedWindowQuery } from "./queries";
import { ERROR, HEADING, HINT, SECTION } from "./styles";
import { UnattendedWindowForm, windowWords } from "./UnattendedWindowForm";

export function UnattendedSection() {
  const loaded = useQuery(unattendedWindowQuery());
  const data = loaded.data;
  return (
    <section aria-labelledby="unattended-title" className={SECTION}>
      <h2 id="unattended-title" className={HEADING}>
        Unattended runs
      </h2>
      <p className={HINT}>
        AI tasks you mark &ldquo;Run unattended&rdquo; run in this window, and
        their results wait for you in the morning review. Tasks made from
        outside content never run unattended.
      </p>
      {data ? (
        <>
          <p className="text-sm">
            {data.window === null
              ? "No window: nothing runs unattended."
              : `Runs ${windowWords(data.window)}.`}
          </p>
          <UnattendedWindowForm
            loaded={data}
            projectId={null}
            offText="Turn off"
          />
        </>
      ) : loaded.isError ? (
        <p role="alert" className={ERROR}>
          The unattended window could not be loaded.
        </p>
      ) : (
        <p className={HINT}>Loading…</p>
      )}
    </section>
  );
}

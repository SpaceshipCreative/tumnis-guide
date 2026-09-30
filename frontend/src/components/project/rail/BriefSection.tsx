// The brief (P0-24, FR-2.7): plain Markdown in a text field (Tiptap arrives with P1-17),
// saved as the project's text entry with the version read.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { useSaveBrief } from "../mutations";
import { briefQuery } from "../queries";
import type { Brief } from "../types";
import { fieldClass, RailSection, saveClass } from "./RailSection";

/** The first non-empty line, without Markdown heading marks. */
export function firstLine(markdown: string | null | undefined): string | null {
  const line = (markdown ?? "")
    .split("\n")
    .map((l) => l.replace(/^#+\s*/, "").trim())
    .find((l) => l !== "");
  return line ?? null;
}

function BriefEditor({
  projectId,
  brief,
}: {
  projectId: string;
  brief: Brief;
}) {
  const [text, setText] = useState(brief.body_md ?? "");
  const save = useSaveBrief();
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate({ projectId, brief, bodyMd: text });
      }}
    >
      <textarea
        aria-label="Brief"
        rows={8}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
        }}
        className={fieldClass}
      />
      <button type="submit" disabled={save.isPending} className={saveClass}>
        Save brief
      </button>
      {save.isSuccess && <p className="text-xs text-muted">Saved</p>}
    </form>
  );
}

export function BriefSection({
  projectId,
  open,
  onToggle,
}: {
  projectId: string;
  open: boolean;
  onToggle: () => void;
}) {
  const brief = useQuery(briefQuery(projectId));
  const summary = brief.isPending
    ? "Loading…"
    : (firstLine(brief.data?.body_md) ?? "No brief yet");
  return (
    <RailSection
      title="Brief"
      summary={summary}
      open={open}
      onToggle={onToggle}
    >
      {brief.data ? (
        <BriefEditor
          key={`${brief.data.id}:${String(brief.data.version)}`}
          projectId={projectId}
          brief={brief.data}
        />
      ) : (
        <p className="text-sm text-muted">
          {brief.isPending ? "Loading…" : "The brief is not ready yet."}
        </p>
      )}
    </RailSection>
  );
}

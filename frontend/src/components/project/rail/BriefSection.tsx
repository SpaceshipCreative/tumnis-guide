// The brief (P0-24, FR-2.7; P1-17): Markdown in the note editor (loaded on demand,
// LazyMarkdownField), saved with "Save brief" as the project's text entry with the
// version read. The save lives in the section, not the editor: a save answers a new
// version, which re-creates the editor, and "Saved" must outlast that.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { useSaveBrief } from "../mutations";
import { briefQuery } from "../queries";
import type { Brief } from "../types";
import { LazyMarkdownField } from "../../../editor/LazyEditor";
import { RailSection, saveClass } from "./RailSection";

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
  save,
}: {
  projectId: string;
  brief: Brief;
  save: ReturnType<typeof useSaveBrief>;
}) {
  const [text, setText] = useState(brief.body_md ?? "");
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate({ projectId, brief, bodyMd: text });
      }}
    >
      <LazyMarkdownField
        markdown={brief.body_md ?? ""}
        label="Brief"
        projectId={projectId}
        onChange={(markdown) => {
          // Saved without the editor's final newline, as the brief's text was before
          // the editor (Scott decision 50); a note keeps ADR-0008's canonical form.
          setText(markdown.replace(/\n$/, ""));
        }}
      />
      <button type="submit" disabled={save.isPending} className={saveClass}>
        Save brief
      </button>
      {save.isSuccess && save.data?.id === brief.id && (
        <p className="text-xs text-muted">Saved</p>
      )}
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
  const save = useSaveBrief();
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
          save={save}
        />
      ) : (
        <p className="text-sm text-muted">
          {brief.isPending ? "Loading…" : "The brief is not ready yet."}
        </p>
      )}
    </RailSection>
  );
}

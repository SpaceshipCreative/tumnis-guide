// The editor behind a dynamic import (P1-17, PERF-2): Tiptap and its Markdown parser
// load only when a screen opens a note or the brief, never with the initial bundle.
import { lazy, Suspense } from "react";

import { HINT } from "../components/common/ui";
import type { EditorProps, MarkdownFieldProps } from "./Editor";

const Editor = lazy(() => import("./Editor"));
const MarkdownField = lazy(() =>
  import("./Editor").then((m) => ({ default: m.MarkdownField })),
);

const opening = <p className={HINT}>Opening the editor…</p>;

/** A knowledge note that saves as it is edited. */
export function LazyEditor(props: EditorProps) {
  return (
    <Suspense fallback={opening}>
      <Editor {...props} />
    </Suspense>
  );
}

/** A Markdown field whose caller saves (the brief). */
export function LazyMarkdownField(props: MarkdownFieldProps) {
  return (
    <Suspense fallback={opening}>
      <MarkdownField {...props} />
    </Suspense>
  );
}

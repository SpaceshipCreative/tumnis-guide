// The note editor (P1-17, ADR-0008). Spec stub: implemented next.
import type { Editor as TiptapEditor } from "@tiptap/core";

export interface EditorProps {
  document: {
    id: string;
    title: string;
    body_md: string | null;
    version: number;
  };
  projectId: string | null;
  onReady?: (editor: TiptapEditor) => void;
}

export default function Editor(props: EditorProps) {
  return <p>{props.document.title}</p>;
}

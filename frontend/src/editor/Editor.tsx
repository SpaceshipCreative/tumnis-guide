// The note editor (P1-17, ADR-0008, FR-15.1): Tiptap over Markdown, loaded only through
// a dynamic import (LazyEditor) so it never counts against the initial bundle (PERF-2).
// `@` mentions a task, `#` a document, `/` opens the block menu ("Make task" in a project
// note). A note the editor cannot keep exactly (canEditSafely false, a table say) opens
// as Markdown source in a textarea, with a notice, instead of being rewritten.
import type { Editor as TiptapEditor } from "@tiptap/core";
import { EditorContent, useEditor } from "@tiptap/react";
import { useMemo, useRef, useState } from "react";

import {
  knowledgeListDocuments,
  knowledgeSearch,
  searchTypeaheadTasks,
} from "../api/sdk.gen";
import type { DocumentDto } from "../api/types.gen";
import { FIELD, HINT } from "../components/common/ui";
import { buildExtensions } from "./extensions";
import type { SuggestHandlers, SuggestItem, SuggestOpen } from "./links";
import { parseTumnisHref } from "./links";
import { makeTask } from "./makeTask";
import {
  canonicalBody,
  editorMode,
  joinFrontmatter,
  splitFrontmatter,
} from "./markdown";
import { useNoteSave, type SaveStatus } from "./useNoteSave";

const SUGGEST_LIMIT = 8;

export interface EditorProps {
  document: Pick<DocumentDto, "id" | "title" | "body_md" | "version">;
  /** The note's project: task mentions and Make task are scoped to it. */
  projectId: string | null;
  /** The editor's accessible name. */
  label?: string;
  /** Where `@` and `#` look; the API by default. */
  suggest?: Pick<SuggestHandlers, "tasks" | "docs">;
  onSaved?: (saved: DocumentDto) => void;
  /** A tumnis task or document link was clicked. */
  onOpenLink?: (link: { kind: "task" | "doc"; id: string }) => void;
  /** The Tiptap editor, once it exists. */
  onReady?: (editor: TiptapEditor) => void;
}

/** `@` and `#` from the API: task typeahead in the project, knowledge search. */
export function apiSuggest(
  projectId: string | null,
): Pick<SuggestHandlers, "tasks" | "docs"> {
  return {
    tasks: async (q) => {
      const { data } = await searchTypeaheadTasks({
        query: { q, project_id: projectId, limit: SUGGEST_LIMIT },
        throwOnError: true,
      });
      return data.map((hit) => ({ id: hit.entity_id, title: hit.title }));
    },
    docs: async (q) => {
      if (q.trim() === "") {
        const { data } = await knowledgeListDocuments({
          query: { project_id: projectId, limit: SUGGEST_LIMIT },
          throwOnError: true,
        });
        return data.items.map((d) => ({ id: d.id, title: d.title }));
      }
      const { data } = await knowledgeSearch({
        query: { q, project_id: projectId, limit: SUGGEST_LIMIT },
        throwOnError: true,
      });
      const seen = new Map<string, SuggestItem>();
      for (const hit of data.items) {
        if (!seen.has(hit.document_id)) {
          seen.set(hit.document_id, {
            id: hit.document_id,
            title: hit.document_title,
          });
        }
      }
      return [...seen.values()];
    },
  };
}

const STATUS_TEXT: Record<SaveStatus, string> = {
  idle: "",
  saving: "Saving…",
  saved: "Saved",
  conflict: "Someone else changed this note. Reload it to see their version.",
  error: "Not saved. Your changes are still here; edit again to retry.",
};

function SaveState({ status }: { status: SaveStatus }) {
  return (
    <p
      role="status"
      className={
        status === "conflict" || status === "error"
          ? "text-sm text-danger"
          : HINT
      }
    >
      {STATUS_TEXT[status]}
    </p>
  );
}

/** The open menu under the editor: arrow keys move, Enter picks, Escape closes. */
function SuggestMenu({ menu, active }: { menu: SuggestOpen; active: number }) {
  const label =
    menu.kind === "task"
      ? "Tasks"
      : menu.kind === "doc"
        ? "Documents"
        : "Insert";
  if (menu.items.length === 0) {
    return <p className={HINT}>No matches</p>;
  }
  return (
    <div
      role="listbox"
      aria-label={label}
      className="flex max-h-60 flex-col overflow-y-auto rounded-md border border-border bg-surface p-1 shadow-sm"
    >
      {menu.items.map((item, i) => (
        <button
          key={item.id}
          type="button"
          role="option"
          aria-selected={i === active}
          onMouseDown={(event) => {
            event.preventDefault(); // keep the editor's selection
            menu.select(item);
          }}
          className="min-h-11 rounded px-2 text-left text-sm aria-selected:bg-accent-soft md:min-h-8"
        >
          {item.title}
        </button>
      ))}
    </div>
  );
}

/** A Markdown field: the rich editor, or the source when the editor would lose something. */
export interface MarkdownFieldProps {
  /** The stored Markdown as loaded, frontmatter included. */
  markdown: string;
  /** The field's accessible name. */
  label: string;
  /** Task mentions and Make task are scoped to this project. */
  projectId: string | null;
  /** Where `@` and `#` look; the API by default. */
  suggest?: Pick<SuggestHandlers, "tasks" | "docs">;
  /** An edit, with the Markdown to store: canonical from the rich editor (frontmatter
   * kept as loaded), exactly as typed in the source view. Opening never calls it. */
  onChange: (markdown: string) => void;
  /** A tumnis task or document link was clicked. */
  onOpenLink?: (link: { kind: "task" | "doc"; id: string }) => void;
  /** The Tiptap editor, once it exists (the rich editor only). */
  onReady?: (editor: TiptapEditor) => void;
}

function RichField({
  markdown,
  label,
  projectId,
  suggest,
  onChange,
  onOpenLink,
  onReady,
}: MarkdownFieldProps) {
  const [menu, setMenu] = useState<SuggestOpen | null>(null);
  const [active, setActive] = useState(0);
  const [taskFailed, setTaskFailed] = useState(false);
  const menuRef = useRef<{ menu: SuggestOpen | null; active: number }>({
    menu: null,
    active: 0,
  });
  menuRef.current = { menu, active };
  // The editor keeps the callbacks it was made with; these read the latest props.
  const latest = useRef({ onChange, onOpenLink });
  latest.current = { onChange, onOpenLink };
  const { frontmatter, body } = useMemo(
    () => splitFrontmatter(markdown),
    [markdown],
  );
  const lookups = useMemo(
    () => suggest ?? apiSuggest(projectId),
    [suggest, projectId],
  );

  const onKeyDown = (event: KeyboardEvent): boolean => {
    const current = menuRef.current.menu;
    if (!current || current.items.length === 0) return false;
    const count = current.items.length;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((i) => (i + step + count) % count);
      return true;
    }
    if (event.key === "Enter" || event.key === "Tab") {
      const item = current.items[menuRef.current.active] ?? current.items[0];
      if (item) current.select(item);
      return true;
    }
    if (event.key === "Escape") {
      setMenu(null);
      return true;
    }
    return false;
  };
  const onOpen = (open: SuggestOpen | null) => {
    setMenu(open);
    setActive(0);
  };

  const editor = useEditor({
    extensions: buildExtensions({
      suggest: { ...lookups, onOpen, onKeyDown },
      slash: {
        onOpen,
        onKeyDown,
        ...(projectId
          ? {
              makeTask: (e: TiptapEditor) => {
                setTaskFailed(false);
                makeTask(e, { projectId }).catch(() => {
                  setTaskFailed(true);
                });
              },
            }
          : {}),
      },
    }),
    content: body,
    contentType: "markdown",
    // No injected <style> tag: the CSP allows only same-origin stylesheets (SEC-4), so
    // styles.css carries Tiptap's base rules instead (APP-08).
    injectCSS: false,
    editorProps: {
      attributes: {
        role: "textbox",
        "aria-multiline": "true",
        "aria-label": label,
        class: `${FIELD} tg-prose min-h-40`,
      },
      handleClickOn: (_view, _pos, _node, _nodePos, event) => {
        const anchor = (event.target as HTMLElement | null)?.closest("a");
        const link = anchor
          ? parseTumnisHref(anchor.getAttribute("href") ?? "")
          : null;
        if (!link) return false;
        latest.current.onOpenLink?.(link);
        return true;
      },
    },
    onCreate: ({ editor: created }) => {
      onReady?.(created);
    },
    onUpdate: ({ editor: changed }) => {
      latest.current.onChange(
        joinFrontmatter(frontmatter, canonicalBody(changed.getMarkdown())),
      );
    },
  });

  return (
    <div className="flex flex-col gap-1">
      <EditorContent editor={editor} />
      {menu && <SuggestMenu menu={menu} active={active} />}
      {taskFailed && (
        <p role="alert" className="text-sm text-danger">
          Could not make the task. Try again.
        </p>
      )}
    </div>
  );
}

function SourceField({ markdown, label, onChange }: MarkdownFieldProps) {
  const [text, setText] = useState(markdown);
  return (
    <div className="flex flex-col gap-1">
      <p className={HINT}>
        This text has formatting the editor cannot keep, such as a table, so it
        opens as Markdown.
      </p>
      <textarea
        aria-label={label}
        rows={12}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          onChange(event.target.value);
        }}
        className={`${FIELD} font-mono`}
      />
    </div>
  );
}

export function MarkdownField(props: MarkdownFieldProps) {
  // Decided once per loaded text (callers key the field by id and version).
  const [mode] = useState(() => editorMode(props.markdown));
  return mode === "rich" ? (
    <RichField {...props} />
  ) : (
    <SourceField {...props} />
  );
}

/** A knowledge note, saved as it is edited (useNoteSave). */
export default function Editor({
  document: doc,
  label,
  onSaved,
  ...field
}: EditorProps) {
  const save = useNoteSave(doc, onSaved ? { onSaved } : {});
  return (
    <div className="flex flex-col gap-1">
      <MarkdownField
        {...field}
        markdown={doc.body_md ?? ""}
        label={label ?? doc.title}
        onChange={save.onChange}
      />
      <SaveState status={save.status} />
    </div>
  );
}

// Mentions as links (P1-17, ADR-0008). `@` picks a task and `#` a document; the pick goes
// into the note as ordinary linked text, `[Send invoice](tumnis://task/<id>)`, which the
// Link extension writes back as Markdown with no custom node or serializer. CSS and a
// click handler show `a[href^="tumnis://"]` as chips. The menus are @tiptap/suggestion
// plugins; what they show is the caller's (`onOpen`).
import { Extension, type Editor, type Range } from "@tiptap/core";
import { PluginKey } from "@tiptap/pm/state";
import {
  Suggestion,
  type SuggestionKeyDownProps,
  type SuggestionOptions,
  type SuggestionProps,
} from "@tiptap/suggestion";

export type MentionKind = "task" | "doc";

export interface SuggestItem {
  id: string;
  title: string;
}

/** An open suggestion menu: what it offers and how to pick one. */
export interface SuggestOpen {
  kind: MentionKind | "command";
  items: SuggestItem[];
  select: (item: SuggestItem) => void;
}

export interface SuggestHandlers {
  tasks: (query: string) => Promise<SuggestItem[]>;
  docs: (query: string) => Promise<SuggestItem[]>;
  /** A menu opened or its items changed; null when it closes. */
  onOpen?: (open: SuggestOpen | null) => void;
  /** A key while a menu is open; true when the menu handled it. */
  onKeyDown?: (event: KeyboardEvent) => boolean;
}

const HREF = /^tumnis:\/\/(task|doc)\/([0-9a-fA-F-]{36})$/;

export function tumnisHref(kind: MentionKind, id: string): string {
  return `tumnis://${kind}/${id}`;
}

export function parseTumnisHref(
  href: string,
): { kind: MentionKind; id: string } | null {
  const match = HREF.exec(href);
  if (!match) return null;
  return { kind: match[1] as MentionKind, id: match[2] ?? "" };
}

/** Replaces `range` with `title` linked to the task or document. */
export function insertLinkedText(
  editor: Editor,
  range: Range,
  kind: MentionKind,
  item: SuggestItem,
): boolean {
  return (
    editor
      .chain()
      .focus()
      .insertContentAt(range, [
        {
          type: "text",
          text: item.title,
          marks: [{ type: "link", attrs: { href: tumnisHref(kind, item.id) } }],
        },
      ])
      // Typing on after the chip is plain text, not more of the link.
      .unsetMark("link")
      .run()
  );
}

/** The render hooks of a suggestion menu, reported through `onOpen`. */
export function reportMenu<T extends SuggestItem>(
  kind: SuggestOpen["kind"],
  handlers: Pick<SuggestHandlers, "onOpen" | "onKeyDown">,
): NonNullable<SuggestionOptions<T, T>["render"]> {
  const report = (props: SuggestionProps<T, T>) => {
    handlers.onOpen?.({
      kind,
      items: props.items,
      select: (item) => {
        props.command(item as T);
      },
    });
  };
  return () => ({
    onStart: report,
    onUpdate: report,
    onKeyDown: ({ event }: SuggestionKeyDownProps) =>
      handlers.onKeyDown?.(event) ?? false,
    onExit: () => {
      handlers.onOpen?.(null);
    },
  });
}

function mentionPlugin(
  editor: Editor,
  kind: MentionKind,
  char: string,
  handlers: SuggestHandlers,
) {
  const fetch = kind === "task" ? handlers.tasks : handlers.docs;
  return Suggestion<SuggestItem, SuggestItem>({
    editor,
    char,
    pluginKey: new PluginKey(`mention-${kind}`),
    items: ({ query }) => fetch(query),
    command: ({ editor: e, range, props }) => {
      insertLinkedText(e, range, kind, props);
    },
    render: reportMenu<SuggestItem>(kind, handlers),
  });
}

/** `@` for tasks and `#` for documents, each inserting a tumnis link. */
export const MentionLinks = Extension.create<{
  handlers: SuggestHandlers | null;
}>({
  name: "mentionLinks",
  addOptions() {
    return { handlers: null };
  },
  addProseMirrorPlugins() {
    const { handlers } = this.options;
    if (!handlers) return [];
    return [
      mentionPlugin(this.editor, "task", "@", handlers),
      mentionPlugin(this.editor, "doc", "#", handlers),
    ];
  },
});

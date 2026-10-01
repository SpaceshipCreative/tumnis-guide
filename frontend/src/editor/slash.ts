// The slash menu (P1-17): `/` at the start of a word offers block types and, in a
// project's note, "Make task" for the checklist item under the cursor. Built on
// @tiptap/suggestion like the mention menus, and shown by the same `onOpen` callback.
import { Extension, type Editor, type Range } from "@tiptap/core";
import { PluginKey } from "@tiptap/pm/state";
import { Suggestion } from "@tiptap/suggestion";

import { reportMenu, type SuggestHandlers, type SuggestItem } from "./links";

export interface SlashItem extends SuggestItem {
  run: (editor: Editor, range: Range) => void;
}

export interface SlashOptions extends Pick<
  SuggestHandlers,
  "onOpen" | "onKeyDown"
> {
  /** Makes a task of the checklist item under the cursor; absent outside a project. */
  makeTask?: (editor: Editor) => void;
}

const block =
  (apply: (editor: Editor) => void) => (editor: Editor, range: Range) => {
    editor.chain().focus().deleteRange(range).run();
    apply(editor);
  };

export function slashItems(opts: Pick<SlashOptions, "makeTask">): SlashItem[] {
  const items: SlashItem[] = [
    {
      id: "h1",
      title: "Heading 1",
      run: block((e) => e.chain().setNode("heading", { level: 1 }).run()),
    },
    {
      id: "h2",
      title: "Heading 2",
      run: block((e) => e.chain().setNode("heading", { level: 2 }).run()),
    },
    {
      id: "h3",
      title: "Heading 3",
      run: block((e) => e.chain().setNode("heading", { level: 3 }).run()),
    },
    {
      id: "bullets",
      title: "Bulleted list",
      run: block((e) => e.chain().toggleBulletList().run()),
    },
    {
      id: "numbers",
      title: "Numbered list",
      run: block((e) => e.chain().toggleOrderedList().run()),
    },
    {
      id: "checklist",
      title: "Checklist",
      run: block((e) => e.chain().toggleTaskList().run()),
    },
    {
      id: "quote",
      title: "Quote",
      run: block((e) => e.chain().toggleBlockquote().run()),
    },
    {
      id: "code",
      title: "Code block",
      run: block((e) => e.chain().toggleCodeBlock().run()),
    },
    {
      id: "divider",
      title: "Divider",
      run: block((e) => e.chain().setHorizontalRule().run()),
    },
  ];
  const { makeTask } = opts;
  if (makeTask) {
    items.push({ id: "task", title: "Make task", run: block(makeTask) });
  }
  return items;
}

export const SlashCommands = Extension.create<{ slash: SlashOptions | null }>({
  name: "slashCommands",
  addOptions() {
    return { slash: null };
  },
  addProseMirrorPlugins() {
    const { slash } = this.options;
    if (!slash) return [];
    const all = slashItems(slash);
    return [
      Suggestion<SlashItem, SlashItem>({
        editor: this.editor,
        char: "/",
        pluginKey: new PluginKey("slashCommands"),
        items: ({ query }) =>
          all.filter((item) =>
            item.title.toLowerCase().includes(query.toLowerCase()),
          ),
        command: ({ editor, range, props }) => {
          props.run(editor, range);
        },
        render: reportMenu<SlashItem>("command", slash),
      }),
    ];
  },
});

// The editor's extensions (P1-17, ADR-0008; Tiptap 3, pinned and upgraded only with the
// round-trip suite green). StarterKit's own Link is off so the one configured here is the
// only one: it knows the `tumnis` scheme for mentions and never opens on click. Its
// `isAllowedUri` accepts only http(s), mailto and tumnis task/doc links: pasted HTML and
// autolinks with any other href never become links, and a link loaded from Markdown with
// one (a `javascript:` URL, say) renders with an empty href while its Markdown is kept
// as written. Pasted HTML only keeps what the schema models. `headless` (the round trip)
// leaves the menus out.
import type { AnyExtension } from "@tiptap/core";
import Link from "@tiptap/extension-link";
import { TaskItem, TaskList } from "@tiptap/extension-list";
import { Markdown } from "@tiptap/markdown";
import StarterKit from "@tiptap/starter-kit";

import { MentionLinks, type SuggestHandlers } from "./links";
import { SlashCommands, type SlashOptions } from "./slash";

const ALLOWED_LINK = /^(https?:\/\/|mailto:|tumnis:\/\/(task|doc)\/)/i;

export function isAllowedLink(url: string): boolean {
  return ALLOWED_LINK.test(url.trim());
}

export function buildExtensions(opts: {
  suggest?: SuggestHandlers;
  slash?: SlashOptions;
  headless?: boolean;
}): AnyExtension[] {
  const extensions: AnyExtension[] = [
    StarterKit.configure({ link: false, trailingNode: false }),
    TaskList,
    TaskItem.configure({ nested: true }),
    Link.configure({
      protocols: ["tumnis"],
      openOnClick: false,
      autolink: true,
      isAllowedUri: (url, ctx) =>
        ctx.defaultValidate(url) && isAllowedLink(url),
    }),
    Markdown.configure({
      indentation: { style: "space", size: 2 },
      markedOptions: { gfm: true },
    }),
  ];
  if (!opts.headless) {
    if (opts.suggest) {
      extensions.push(MentionLinks.configure({ handlers: opts.suggest }));
    }
    if (opts.slash)
      extensions.push(SlashCommands.configure({ slash: opts.slash }));
  }
  return extensions;
}

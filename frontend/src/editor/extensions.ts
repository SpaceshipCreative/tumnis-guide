// The editor's extensions (P1-17, ADR-0008). Spec stub: implemented next.
import type { AnyExtension } from "@tiptap/core";
import type {} from "@tiptap/markdown";

import type { SuggestHandlers } from "./links";

export function buildExtensions(opts: {
  suggest?: SuggestHandlers;
  headless?: boolean;
}): AnyExtension[] {
  throw new Error(`not implemented: ${String(opts.headless)}`);
}

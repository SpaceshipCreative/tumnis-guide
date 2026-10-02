// Markdown in and out of the editor (P1-17, ADR-0008). Markdown is the stored form: a
// note loads into Tiptap and saves back through @tiptap/markdown, in the canonical style
// (`-` bullets, `**` bold, `*` italic, `#` headings, one blank line between blocks, one
// newline at the end). Frontmatter at byte 0 is cut off before the editor sees the body
// and put back unchanged. A body the editor cannot model (a table, say) is not rewritten:
// `canEditSafely` compares the Markdown token trees of the body before and after a round
// trip, and such a note opens as source.
import { Editor } from "@tiptap/core";
import { Lexer, type Token } from "marked";

import { buildExtensions } from "./extensions";

/** `---` on the first line, then YAML, then a closing `---` line. */
const FRONTMATTER = /^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/;

export function splitFrontmatter(src: string): {
  frontmatter: string | null;
  body: string;
} {
  const match = FRONTMATTER.exec(src);
  if (!match) return { frontmatter: null, body: src };
  return { frontmatter: match[0], body: src.slice(match[0].length) };
}

export function joinFrontmatter(
  frontmatter: string | null,
  body: string,
): string {
  return (frontmatter ?? "") + body;
}

/** The editor's Markdown with exactly one newline at the end (none for an empty note). */
export function canonicalBody(markdown: string): string {
  const body = markdown.replace(/\n+$/, "");
  return body === "" ? "" : `${body}\n`;
}

/** A headless editor's Markdown for `body`: the canonical form of the body. */
function bodyRoundTrip(body: string): string {
  const editor = new Editor({
    extensions: buildExtensions({ headless: true }),
    content: body,
    contentType: "markdown",
    injectCSS: false, // never a <style> tag under the CSP (APP-08)
  });
  try {
    return canonicalBody(editor.getMarkdown());
  } finally {
    editor.destroy();
  }
}

export function roundTrip(src: string): string {
  const { frontmatter, body } = splitFrontmatter(src);
  return joinFrontmatter(frontmatter, bodyRoundTrip(body));
}

type Shape =
  string | number | boolean | null | Shape[] | { [k: string]: Shape };

/**
 * What a token means, without how it was written: `raw` (the source text) goes, and so
 * does `text` where child `tokens` carry the same content (`__a__` and `**a**` give the
 * same `strong`); blank-line `space` tokens go too.
 */
function shape(value: unknown): Shape {
  if (Array.isArray(value)) {
    return value
      .filter((v) => !(isToken(v) && v.type === "space"))
      .map((v) => shape(v));
  }
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const out: Record<string, Shape> = {};
    for (const [key, v] of Object.entries(record)) {
      if (key === "raw") continue;
      if (key === "text" && "tokens" in record) continue;
      out[key] = shape(v);
    }
    return out;
  }
  if (typeof value === "string" || typeof value === "number") return value;
  if (typeof value === "boolean") return value;
  return null;
}

function isToken(value: unknown): value is Token {
  return value !== null && typeof value === "object" && "type" in value;
}

function tokens(markdown: string): Shape {
  return shape(new Lexer({ gfm: true }).lex(markdown));
}

/** True when the editor keeps everything the body says (only the spelling may change). */
export function canEditSafely(src: string): boolean {
  const { body } = splitFrontmatter(src);
  try {
    return (
      JSON.stringify(tokens(body)) ===
      JSON.stringify(tokens(bodyRoundTrip(body)))
    );
  } catch {
    return false;
  }
}

/** How a note opens: in the rich editor, or as Markdown source when that would lose something. */
export function editorMode(src: string): "rich" | "source" {
  return canEditSafely(src) ? "rich" : "source";
}

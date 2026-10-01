// Markdown in and out of the editor (P1-17, ADR-0008). Spec stub: implemented next.
export function splitFrontmatter(src: string): {
  frontmatter: string | null;
  body: string;
} {
  throw new Error(`not implemented: ${String(src.length)}`);
}

export function joinFrontmatter(
  frontmatter: string | null,
  body: string,
): string {
  throw new Error(`not implemented: ${String(frontmatter)}${body}`);
}

export function roundTrip(src: string): string {
  throw new Error(`not implemented: ${src}`);
}

export function canEditSafely(src: string): boolean {
  throw new Error(`not implemented: ${src}`);
}

export function editorMode(src: string): "rich" | "source" {
  throw new Error(`not implemented: ${src}`);
}

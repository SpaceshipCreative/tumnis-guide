// Mentions as links (P1-17, ADR-0008). Spec stub: implemented next.
export interface SuggestItem {
  id: string;
  title: string;
}

/** An open suggestion menu: its items and how to pick one. */
export interface SuggestOpen {
  kind: "task" | "doc";
  items: SuggestItem[];
  select: (item: SuggestItem) => void;
}

export interface SuggestHandlers {
  tasks: (query: string) => Promise<SuggestItem[]>;
  docs: (query: string) => Promise<SuggestItem[]>;
  /** The menu opened or its items changed; null when it closes. */
  onOpen?: (open: SuggestOpen | null) => void;
}

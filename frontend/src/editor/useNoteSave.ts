// Saving a note (P1-17, ADR-0008). Opening a note never writes: a save goes out only
// after an edit, and only when the note's Markdown then differs from the canonical form
// of what was loaded, so a note in another Markdown style changes format at most once, on
// its first edit. Saves are debounced, carry the version read (409 shows a conflict), and
// one is in flight at a time; a pending change is sent when the editor closes.
import { useCallback, useEffect, useRef, useState } from "react";

import type { DocumentDto } from "../api/types.gen";
import { zDocumentDto } from "../api/zod.gen";
import { apiWrite, ConflictError } from "../lib/fetch";
import { editorMode, roundTrip } from "./markdown";

export const SAVE_DEBOUNCE_MS = 400;

export type SaveStatus = "idle" | "saving" | "saved" | "conflict" | "error";

export interface NoteSave {
  /** An edit, with the Markdown to store (MarkdownField's onChange). */
  onChange: (markdown: string) => void;
  status: SaveStatus;
}

export function useNoteSave(
  doc: Pick<DocumentDto, "id" | "body_md" | "version">,
  opts: { onSaved?: (saved: DocumentDto) => void } = {},
): NoteSave {
  const loaded = doc.body_md ?? "";
  // What the note already is, as far as a save is concerned.
  const saved = useRef<string | null>(null);
  // A rich note's baseline is its canonical form (opening never rewrites it); a note
  // shown as source is compared with its text as stored.
  saved.current ??= editorMode(loaded) === "rich" ? roundTrip(loaded) : loaded;
  const raw = useRef(loaded);
  const version = useRef(doc.version);
  const pending = useRef<string | null>(null);
  // Read through a call: a change typed while a save is in flight sets it meanwhile.
  const queued = (): boolean => pending.current !== null;
  const inFlight = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onSaved = useRef(opts.onSaved);
  onSaved.current = opts.onSaved;
  const [status, setStatus] = useState<SaveStatus>("idle");

  const flush = useCallback(async (): Promise<void> => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const next = pending.current;
    if (next === null || inFlight.current) return;
    pending.current = null;
    if (next === saved.current || next === raw.current) return;
    inFlight.current = true;
    setStatus("saving");
    try {
      const out = await apiWrite({
        kind: "update",
        method: "PATCH",
        path: `/knowledge/documents/${doc.id}`,
        body: { body_md: next },
        version: version.current,
        idempotencyKey: crypto.randomUUID(),
        schema: zDocumentDto,
      });
      version.current = out.version;
      saved.current = next;
      raw.current = next;
      setStatus("saved");
      onSaved.current?.(out);
    } catch (error) {
      setStatus(error instanceof ConflictError ? "conflict" : "error");
    } finally {
      inFlight.current = false;
      if (queued()) void flush();
    }
  }, [doc.id]);

  const schedule = useCallback(
    (next: string) => {
      pending.current = next;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        void flush();
      }, SAVE_DEBOUNCE_MS);
    },
    [flush],
  );

  // A change still waiting when the editor closes goes out then.
  useEffect(
    () => () => {
      if (pending.current !== null) void flush();
    },
    [flush],
  );

  return { onChange: schedule, status };
}

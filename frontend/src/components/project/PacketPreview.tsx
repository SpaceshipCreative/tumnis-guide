// The packet preview (P1-17, FR-15.4): what an enrichment run for this task would get,
// read from `GET /v1/tasks/{id}/packet?kind=enrich` (nothing is dispatched). The brief
// comes first, then each knowledge passage with its citation, "Rate card, page 2"; each
// is an article, so the brief and every passage read as separate parts.
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { agentsGetTaskPacketOptions } from "../../api/@tanstack/react-query.gen";
import { ERROR_TEXT, HINT } from "../common/ui";

interface PassageView {
  document_id: string;
  title: string;
  heading_path: string[];
  page: number | null;
  text: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
}

/** The brief and passages of an enrich packet's body (the request's own shape). */
export function packetKnowledge(body: unknown): {
  brief: string;
  passages: PassageView[];
} {
  const record = asRecord(body);
  const brief = typeof record.brief === "string" ? record.brief : "";
  const raw = Array.isArray(record.passages) ? record.passages : [];
  const passages = raw.flatMap((item): PassageView[] => {
    const p = asRecord(item);
    if (typeof p.text !== "string" || typeof p.title !== "string") return [];
    return [
      {
        document_id: typeof p.document_id === "string" ? p.document_id : "",
        title: p.title,
        heading_path: Array.isArray(p.heading_path)
          ? p.heading_path.filter((h): h is string => typeof h === "string")
          : [],
        page: typeof p.page === "number" ? p.page : null,
        text: p.text,
      },
    ];
  });
  return { brief, passages };
}

export function citation(p: Pick<PassageView, "title" | "page">): string {
  return p.page === null ? p.title : `${p.title}, page ${String(p.page)}`;
}

// The preview reads only the packet's body, and reads it defensively (packetKnowledge),
// so it checks only that much: a change elsewhere in the run envelope (timeouts, skill
// names, callback) must not blank the preview.
const zPreviewPacket = z.object({ body: z.record(z.string(), z.unknown()) });

export function PacketPreview({ taskId }: { taskId: string }) {
  const packet = useQuery(
    agentsGetTaskPacketOptions({
      path: { task_id: taskId },
      query: { kind: "enrich" },
      responseValidator: async (data) => {
        await zPreviewPacket.parseAsync(data);
      },
    }),
  );
  const { brief, passages } = packetKnowledge(packet.data?.body);
  return (
    <section aria-label="Packet preview" className="flex flex-col gap-2">
      {packet.isPending ? (
        <p className={HINT}>Loading the packet…</p>
      ) : packet.isError ? (
        <p className={ERROR_TEXT}>The packet could not be loaded.</p>
      ) : (
        <>
          <article className="flex flex-col gap-1">
            <h3 className="text-sm font-semibold">Brief</h3>
            {brief === "" ? (
              <p className={HINT}>No brief yet.</p>
            ) : (
              <p className="text-sm whitespace-pre-line">{brief}</p>
            )}
          </article>
          <div className="flex flex-col gap-1">
            <h3 className="text-sm font-semibold">Passages</h3>
            {passages.length === 0 ? (
              <p className={HINT}>No passages match this task.</p>
            ) : (
              <ol className="flex flex-col gap-2">
                {passages.map((p, i) => (
                  <li key={`${p.document_id}:${String(i)}`}>
                    <article className="flex flex-col gap-0.5">
                      <p className="text-xs font-medium text-muted">
                        {citation(p)}
                      </p>
                      <p className="text-sm whitespace-pre-line">{p.text}</p>
                    </article>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </>
      )}
    </section>
  );
}

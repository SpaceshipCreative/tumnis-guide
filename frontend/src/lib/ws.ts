// Live socket (P0-22). Spec stub.
import type { QueryClient } from "@tanstack/react-query";

export function connectLive(qc: QueryClient, url?: string): () => void {
  throw new Error(
    `connectLive ${url ?? ""} ${String(qc.isFetching())}: not implemented (spec:P0-22)`,
  );
}

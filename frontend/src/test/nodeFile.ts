// File uploads under Vitest's jsdom environment on Node 24 (P1-17's upload test). The
// jsdom environment makes `File`, `Blob` and `FormData` jsdom's, while `fetch` and
// `Request` stay Node's (undici). Vitest converts a jsdom FormData when a Request is
// built, but on Node 24.6+ the file part comes out wrong (its name lost, its length not
// matching the header, or the conversion throwing on jsdom 30.1), so the request fails.
// Upstream: vitest-dev/vitest#11300 (open on 2026-10-01); delete this file once a Vitest
// release carries the fix. Until then the tests use Node's own `File`, `Blob` and
// `FormData`, the classes `fetch` itself uses, as a browser has only one of each. No test
// hands these to a jsdom API that needs jsdom's own Blob (FileReader, say).
import { Blob as NodeBlob, File as NodeFile } from "node:buffer";

export async function useNodeFileClasses(): Promise<void> {
  // Node's FormData is not importable; a Response parses one (before the swap below).
  const parsed = await new Response(new URLSearchParams("probe=1")).formData();
  const NodeFormData = parsed.constructor as typeof FormData;
  // Own data properties on the global, in place of the environment's accessors that
  // read jsdom's window, so Vitest's own conversion still sees jsdom's classes.
  for (const [name, value] of [
    ["Blob", NodeBlob],
    ["File", NodeFile],
    ["FormData", NodeFormData],
  ] as const) {
    Object.defineProperty(globalThis, name, {
      value,
      writable: true,
      configurable: true,
    });
  }
}

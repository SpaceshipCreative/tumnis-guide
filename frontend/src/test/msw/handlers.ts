import { http, HttpResponse, type RequestHandler } from "msw";

/**
 * `GET /v1/review/kinds` answering these registered kinds (the P0-18 registry). The
 * response shape lives here, not in the tests, so it can follow the generated schema.
 */
export function reviewKinds(kinds: readonly string[]): RequestHandler {
  return http.get("/v1/review/kinds", () =>
    HttpResponse.json(kinds.map((kind) => ({ kind }))),
  );
}

// Default handlers shared by every test: the reads the shell itself makes. Anything
// else fails (setup.ts listens with the "error" strategy); a test declares the other
// endpoints it needs with `server.use(...)`.
export const handlers: RequestHandler[] = [reviewKinds([])];

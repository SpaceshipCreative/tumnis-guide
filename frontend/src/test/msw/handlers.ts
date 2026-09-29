import type { RequestHandler } from "msw";

// Default handlers shared by every test. Empty on purpose: a test declares
// the endpoints it needs with `server.use(...)`, and anything else fails
// (setup.ts listens with the "error" strategy).
export const handlers: RequestHandler[] = [];

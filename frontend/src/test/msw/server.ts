import { setupServer } from "msw/node";

import { handlers } from "./handlers";

// One MSW server for the whole Vitest run; tests add handlers with
// `server.use(...)` and setup.ts resets them after each test.
export const server = setupServer(...handlers);

// Zod without its JIT (P0-16's CSP has no 'unsafe-eval'): with `jitless`, zod never
// probes `new Function`, which a strict CSP reports as a violation even when caught.
// Imported first by main.tsx, before any schema parses.
import * as z from "zod";

z.config({ jitless: true });

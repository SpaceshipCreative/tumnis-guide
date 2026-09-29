// Vitest on top of the Vite config (P0-22): the same plugins, so route files are
// generated and transformed as in the build.
import { defineConfig, mergeConfig } from "vitest/config";

import viteConfig from "./vite.config.ts";

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
      restoreMocks: true,
      unstubGlobals: true,
      // Whole-route journeys (P0-24's undo test renders the project page eight times)
      // run past the 5 s default on a loaded machine.
      testTimeout: 20_000,
      coverage: {
        provider: "v8",
        include: ["src/**"],
        exclude: ["src/api/**", "src/test/**", "src/routeTree.gen.ts"],
      },
    },
  }),
);

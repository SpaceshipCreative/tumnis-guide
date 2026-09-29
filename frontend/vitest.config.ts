// Standalone until P0-22 adds vite.config.ts; then this becomes
// `mergeConfig(viteConfig, defineConfig({ test: ... }))` as in the plan.
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
    coverage: {
      provider: "v8",
      include: ["src/**"],
      exclude: ["src/api/**", "src/test/**"],
    },
  },
});

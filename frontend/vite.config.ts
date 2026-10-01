// Vite build (P0-22, ADR-0003): React, TanStack Router file routes with one chunk
// per route (the entry stays small, PERF-2), Tailwind CSS v4 and the service worker.
// `build.manifest` writes dist/.vite/manifest.json, which scripts/ci/check_bundle.mjs
// walks to measure the initial JavaScript.
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    // Before the React plugin: it rewrites route files into lazy chunks. Not under Vitest:
    // there a lazy route is transformed and loaded inside the test that first visits it,
    // which on a loaded machine alone outlasts `findBy` waits and test budgets. The route
    // modules then load with the test file instead; the build still splits.
    tanstackRouter({
      target: "react",
      autoCodeSplitting: !process.env.VITEST,
      routesDirectory: "./src/routes",
      generatedRouteTree: "./src/routeTree.gen.ts",
      routeFileIgnorePattern: "\\.test\\.",
      quoteStyle: "double",
      semicolons: true,
    }),
    react(),
    tailwindcss(),
    // src/sw.ts is the worker (P4-05: push and notification clicks); vite-plugin-pwa
    // builds it to /sw.js and injects the precache manifest at `self.__WB_MANIFEST`.
    VitePWA({
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      registerType: "autoUpdate",
      // main.tsx registers /sw.js itself: no injected inline script (CSP, P0-16).
      injectRegister: false,
      manifest: {
        name: "Tumnis Guide",
        short_name: "Tumnis",
        description: "Projects, tasks and the agents that help with them.",
        start_url: "/",
        scope: "/",
        display: "standalone",
        background_color: "#0f172a",
        theme_color: "#0f172a",
        icons: [
          { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
          {
            src: "/icons/icon-maskable-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      injectManifest: {
        // Precache the shell and its assets; nothing under /v1 is cached at run time.
        // Inter's Latin file too (DS-01), so the offline shell keeps its font; other
        // scripts load on first use. src/sw.ts answers navigations with /index.html,
        // except the paths in its NOT_THE_SHELL list.
        globPatterns: [
          "**/*.{js,css,html,png,svg,webmanifest}",
          "**/inter-latin-wght-normal-*.woff2",
        ],
      },
    }),
  ],
  build: {
    manifest: true,
    sourcemap: true,
    rolldownOptions: {
      output: {
        // Run modules in source order across chunks (issue #36). Otherwise a shared chunk
        // (the generated zod schemas) evaluates before the entry's first import,
        // lib/zodConfig, and zod probes `new Function` under the strict CSP (P0-16).
        strictExecutionOrder: true,
      },
    },
  },
  server: {
    proxy: {
      "/v1": "http://localhost:8080",
      "/ws": { target: "ws://localhost:8080", ws: true },
    },
  },
});

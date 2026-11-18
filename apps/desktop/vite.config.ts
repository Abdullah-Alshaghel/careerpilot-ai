import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Tauri expects a fixed dev port and must not clear its terminal output.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  // Only VITE_* variables are exposed to the bundle. They are PUBLIC values.
  envPrefix: ["VITE_"],
  envDir: "../..",
  server: {
    port: 1420,
    strictPort: true,
    host: "127.0.0.1",
  },
  build: {
    target: "es2022",
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
});

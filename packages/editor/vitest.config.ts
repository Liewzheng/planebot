import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src/core", import.meta.url)),
    },
  },
  test: {
    environment: "node",
  },
});

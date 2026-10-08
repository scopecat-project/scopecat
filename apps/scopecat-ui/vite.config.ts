import { readFileSync } from "node:fs";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode, command }) => {
  const env = loadEnv(mode, process.cwd(), "");
  let daemonOrigin: string | undefined;
  if (command === "serve" && mode !== "test") {
    if (!env.SCOPECAT_DEV_ENDPOINT_FILE) {
      throw new Error("Start source development with uv run --locked python -m lab_tools.dev.");
    }
    daemonOrigin = "http://127.0.0.1:1";
  }

  return {
    plugins: [react(), tailwindcss()],
    server: {
      proxy: daemonOrigin
        ? {
            "/api": {
              target: daemonOrigin,
              changeOrigin: false,
              configure(_proxy, options) {
                // Update the proxy's original options, not Vite's middleware copy.
                options.bypass = () => {
                  // Read on every request: safe backend restart may change its port.
                  // The launcher atomically replaces this owned local file.
                  const endpoint = JSON.parse(
                    readFileSync(env.SCOPECAT_DEV_ENDPOINT_FILE, "utf8"),
                  ) as { url: string };
                  const url = new URL(endpoint.url);
                  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1") {
                    throw new Error("Invalid development backend endpoint");
                  }
                  options.target = endpoint.url;
                };
              },
            },
          }
        : undefined,
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
      sourcemap: false,
    },
  };
});

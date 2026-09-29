import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode, command }) => {
  const env = loadEnv(mode, process.cwd(), "");
  let daemonOrigin: string | undefined;
  if (command === "serve" && mode !== "test") {
    if (!env.SCOPECAT_DEV_ENDPOINT) {
      throw new Error(
        "Start source development with python -m lab_tools.dev --source /path/to/scopecat.",
      );
    }
    daemonOrigin = env.SCOPECAT_DEV_ENDPOINT;
  }

  return {
    plugins: [react(), tailwindcss()],
    server: {
      proxy: daemonOrigin
        ? {
            "/api": {
              target: daemonOrigin,
              changeOrigin: false,
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

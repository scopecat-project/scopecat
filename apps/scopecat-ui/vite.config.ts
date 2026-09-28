import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode, command }) => {
  const env = loadEnv(mode, process.cwd(), "");
  let daemonOrigin: string | undefined;
  if (command === "serve" && mode !== "test") {
    if (!env.SCOPECAT_APPLICATION_HOME) {
      throw new Error(
        "Set SCOPECAT_APPLICATION_HOME to your development application home, then start that application.",
      );
    }
    const home = resolve(env.SCOPECAT_APPLICATION_HOME);
    const installation = JSON.parse(readFileSync(join(home, "installation.json"), "utf8")) as {
      python: string;
    };
    const status = JSON.parse(
      execFileSync(
        installation.python,
        ["-I", "-m", "lab_tools.application", "--home", home, "--action", "status"],
        { encoding: "utf8", windowsHide: true },
      ),
    ) as { state: string; url: string | null };
    if (status.state !== "running" || !status.url) {
      throw new Error(
        "The development application is not running. Start it explicitly with scopecat app --home HOME --action start.",
      );
    }
    daemonOrigin = status.url;
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

// @vitest-environment node
import { createServer as createHttpServer, type Server } from "node:http";
import { mkdtemp, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { expect, test } from "vitest";

async function listen(server: Server): Promise<string> {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("No loopback port");
  return `http://127.0.0.1:${address.port}`;
}

async function close(server: Server) {
  server.closeAllConnections();
  await new Promise<void>((resolve, reject) =>
    server.close((error) => (error ? reject(error) : resolve())),
  );
}

test("live Vite proxy follows atomic backend endpoint replacement", async () => {
  const directory = await mkdtemp(join(tmpdir(), "scopecat-vite-"));
  const endpoint = join(directory, "backend.json");
  const original = process.env.SCOPECAT_DEV_ENDPOINT_FILE;
  const first = createHttpServer((_req, res) => res.end("first backend"));
  const second = createHttpServer((_req, res) => res.end("restarted backend"));
  const firstUrl = await listen(first);
  const secondUrl = await listen(second);
  process.env.SCOPECAT_DEV_ENDPOINT_FILE = endpoint;
  const vite = await createServer({
    configFile: fileURLToPath(new URL("./vite.config.ts", import.meta.url)),
    mode: "development",
    logLevel: "silent",
    server: { host: "127.0.0.1", port: 0, strictPort: true },
  });
  try {
    await writeFile(endpoint, JSON.stringify({ url: firstUrl }));
    await vite.listen();
    const address = vite.httpServer?.address();
    if (!address || typeof address === "string") throw new Error("No Vite port");
    const ui = `http://127.0.0.1:${address.port}`;
    expect(await (await fetch(`${ui}/api/v1/health`)).text()).toBe("first backend");
    const next = join(directory, "next.json");
    await writeFile(next, JSON.stringify({ url: secondUrl }));
    await rename(next, endpoint);
    expect(await (await fetch(`${ui}/api/v1/health`)).text()).toBe("restarted backend");
    expect(await (await fetch(ui)).text()).toContain("/@vite/client");
  } finally {
    await vite.close();
    await Promise.all([close(first), close(second)]);
    if (original === undefined) delete process.env.SCOPECAT_DEV_ENDPOINT_FILE;
    else process.env.SCOPECAT_DEV_ENDPOINT_FILE = original;
    await rm(directory, { recursive: true, force: true });
  }
}, 30_000);

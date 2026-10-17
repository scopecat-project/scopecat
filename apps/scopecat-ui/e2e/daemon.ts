import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";

export const ROOT = resolve(process.cwd(), "../..");
export function uv(args: string[]) {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", ROOT, ...args], {
    env,
    encoding: "utf8",
    timeout: 60_000,
  });
  if (result.error || result.status !== 0)
    throw new Error(`${result.error?.message ?? ""}\n${result.stdout}\n${result.stderr}`);
  return result.stdout.trim();
}

export function daemon(args: string[]) {
  return uv(["python", "-m", "scopecat_server.cli", ...args]);
}

export async function startProject(project: string): Promise<string> {
  daemon(["start", project, "--port", "0", "--static-dir", resolve("dist")]);
  const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8"));
  return endpoint.base_url;
}

export function stopProject(project: string) {
  daemon(["stop", project]);
}

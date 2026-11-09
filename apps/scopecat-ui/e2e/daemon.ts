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

/** Join the recorded daemon and its owned process tree before removing a Windows fixture. */
export function stopProjectAndWait(project: string) {
  return uv([
    "python",
    "-c",
    `
import json, sys
from pathlib import Path
import httpx2, psutil
from scopecat.application_instance import open_instance
from scopecat_server.lifecycle import _require_selected_binding
from scopecat.daemon.endpoint import (
    DAEMON_SHUTDOWN_PATH, DAEMON_SHUTDOWN_TOKEN_HEADER, read_daemon_endpoint_record,
)
root = Path(sys.argv[1]).resolve()
record = read_daemon_endpoint_record(root)
owned = []
if record is not None:
    _require_selected_binding(open_instance(root, resolve_adapter=False), record)
    try:
        process = psutil.Process(record.pid)
        if abs(process.create_time() - record.process_create_time) >= 0.01:
            raise RuntimeError("Fixture daemon process identity changed")
        owned = [process, *process.children(recursive=True)]
        # Join Windows venv forwarding launchers too, before removing the cwd.
        for parent in process.parents():
            args = parent.cmdline()
            if not (("scopecat_server.cli" in args or "scopecat_server._daemon_entry" in args)
                    and "serve" in args and str(root) in args):
                break
            owned.append(parent)
    except psutil.NoSuchProcess:
        pass
identities = [(p.pid, p.create_time()) for p in owned if p.is_running()]
if record is not None and owned:
    # This is an explicit end of our isolated fixture, not an idle-only close.
    # Request normal shutdown directly so HTTP failure never falls back to kill.
    with httpx2.Client(timeout=10, trust_env=False) as client:
        response = client.post(
            record.base_url.rstrip("/") + DAEMON_SHUTDOWN_PATH,
            headers={DAEMON_SHUTDOWN_TOKEN_HEADER: record.shutdown_token},
        )
        response.raise_for_status()
_, alive = psutil.wait_procs(owned, timeout=10)
alive = [p for p in alive if p.is_running() and p.status() != psutil.STATUS_ZOMBIE]
if alive:
    raise RuntimeError(f"Fixture processes did not stop: {[(p.pid, p.create_time()) for p in alive]}; owned={identities}")
print(json.dumps({"stopped": identities}))
`,
    project,
  ]);
}

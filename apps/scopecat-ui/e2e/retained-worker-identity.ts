import { win32 } from "node:path";

export interface WorkerProcess {
  pid: number;
  ppid: number;
  created: number;
  args: string[];
  executable: string;
}

/** A Windows venv launcher and its interpreter are one invocation, not two workers. */
export function retainedWorkerIdentities(processes: WorkerProcess[], platform = process.platform) {
  const byPid = new Map(processes.map((process) => [process.pid, process]));
  const forwardingParents = new Set<number>();
  const sameInvocation = (a: WorkerProcess, b: WorkerProcess) =>
    platform === "win32" &&
    a.executable.toLowerCase() !== b.executable.toLowerCase() &&
    win32.basename(win32.dirname(a.executable)).toLowerCase() === "scripts" &&
    /^pythonw?\.exe$/i.test(win32.basename(a.executable)) &&
    win32.basename(a.executable).toLowerCase() === win32.basename(b.executable).toLowerCase() &&
    JSON.stringify(a.args.slice(1)) === JSON.stringify(b.args.slice(1));
  for (const process of processes) {
    const parent = byPid.get(process.ppid);
    if (parent && parent.created <= process.created && sameInvocation(parent, process)) {
      if (forwardingParents.has(parent.pid)) {
        throw new Error(`Worker launcher ${parent.pid} has multiple matching interpreters`);
      }
      forwardingParents.add(parent.pid);
    }
  }
  return processes
    .filter((process) => {
      const parent = byPid.get(process.ppid);
      return !parent || parent.created > process.created || !sameInvocation(parent, process);
    })
    .map(({ pid, created }) => ({ pid, created }))
    .sort((a, b) => a.pid - b.pid);
}

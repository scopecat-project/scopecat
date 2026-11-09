// @vitest-environment node
import { expect, test } from "vitest";
import { retainedWorkerIdentities, type WorkerProcess } from "./retained-worker-identity";
const args = ["python", "-m", "scopecat_server.retained_worker", "fixture", "--serve", "rev-a"];
function worker(pid: number, ppid: number, created = pid): WorkerProcess {
  return { pid, ppid, created, args, executable: "C:\\Python\\python.exe" };
}
const launcher = { ...worker(10, 1), executable: "C:\\fixture\\.venv\\Scripts\\python.exe" };
test("counts a single interpreter and a Windows forwarding pair as one invocation", () => {
  expect(retainedWorkerIdentities([worker(10, 1)], "win32")).toEqual([{ pid: 10, created: 10 }]);
  expect(retainedWorkerIdentities([launcher, worker(11, 10)], "win32")).toEqual([
    { pid: 10, created: 10 },
  ]);
});
test("does not hide separate invocations with identical arguments", () => {
  expect(retainedWorkerIdentities([worker(10, 1), worker(20, 1)], "win32")).toHaveLength(2);
});
test("does not fold a same-interpreter child or a non-Windows process", () => {
  expect(retainedWorkerIdentities([worker(10, 1), worker(11, 10)], "win32")).toHaveLength(2);
  expect(retainedWorkerIdentities([launcher, worker(11, 10)], "linux")).toHaveLength(2);
});
test("does not fold a child running a different retained revision", () => {
  expect(
    retainedWorkerIdentities(
      [launcher, { ...worker(11, 10), args: [...args.slice(0, -1), "rev-b"] }],
      "win32",
    ),
  ).toHaveLength(2);
});
test("rejects multiple matching children instead of hiding duplicate execution", () => {
  expect(() =>
    retainedWorkerIdentities([launcher, worker(11, 10), worker(12, 10)], "win32"),
  ).toThrow("multiple matching interpreters");
});
test("retains creation identity and does not follow a recycled parent PID", () => {
  expect(retainedWorkerIdentities([{ ...launcher, created: 20 }, worker(11, 10)], "win32")).toEqual(
    [
      { pid: 10, created: 20 },
      { pid: 11, created: 11 },
    ],
  );
});

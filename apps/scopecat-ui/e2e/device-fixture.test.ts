// @vitest-environment node
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { rm } from "node:fs/promises";
import { afterEach, expect, it, vi } from "vitest";
import { cleanDeviceFixture, startDeviceFixture } from "./device-fixture";
import { uv } from "./daemon";
vi.mock("node:child_process", () => ({ spawn: vi.fn() }));
vi.mock("node:fs/promises", () => ({ rm: vi.fn() }));
vi.mock("./daemon", () => ({ uv: vi.fn(() => "fixture-python") }));
afterEach(() => {
  vi.resetAllMocks();
  vi.useRealTimers();
});
function fakeChild() {
  const child = Object.assign(new EventEmitter(), {
    stdin: new PassThrough(),
    stdout: new PassThrough(),
    stderr: new PassThrough(),
    exitCode: null,
    kill: vi.fn(),
  });
  vi.mocked(spawn).mockReturnValue(child as unknown as ChildProcessWithoutNullStreams);
  vi.mocked(uv).mockReturnValue("fixture-python");
  return child;
}
it("owns the Python child and waits for its graceful close instead of the uv wrapper exit", async () => {
  const child = fakeChild();
  let command = "";
  child.stdin.on("data", (chunk) => {
    command += String(chunk);
  });
  const fixture = startDeviceFixture("fixture.py", "isolated-home");
  expect(vi.mocked(spawn).mock.calls[0]?.[0]).toBe("fixture-python");
  const stop = fixture.stop();
  expect(command).toBe("stop\n");
  let finished = false;
  void stop.then(() => {
    finished = true;
  });
  child.emit("exit", 0);
  await Promise.resolve();
  expect(finished).toBe(false);
  child.emit("close", 0, null);
  await stop;
  expect(child.kill).not.toHaveBeenCalled();
});
it("reports a bounded stop failure and only terminates its owned test process", async () => {
  vi.useFakeTimers();
  const child = fakeChild();
  const fixture = startDeviceFixture("fixture.py", "isolated-home");
  const stop = fixture.stop();
  void stop.catch(() => {});
  await vi.advanceTimersByTimeAsync(15_000);
  await expect(stop).rejects.toThrow("Fixture did not stop normally");
  expect(child.kill).toHaveBeenCalledExactlyOnceWith("SIGTERM");
});
it("preserves the primary assertion while attaching cleanup failure and retaining the test root", async () => {
  const child = fakeChild();
  const fixture = startDeviceFixture("fixture.py", "isolated-home");
  child.emit("close", 0, null);
  vi.mocked(rm).mockRejectedValue(new Error("EBUSY"));
  const attach = vi.fn();
  await cleanDeviceFixture(fixture, "isolated-home", false, { attach });
  expect(attach).toHaveBeenCalledOnce();
  await expect(cleanDeviceFixture(fixture, "isolated-home", true, { attach })).rejects.toThrow(
    "EBUSY",
  );
});

it("retains the fixture root when Python exits nonzero during cleanup", async () => {
  const child = fakeChild();
  const fixture = startDeviceFixture("fixture.py", "isolated-home");
  child.stderr.write("runtime cleanup failed");
  child.emit("close", 1, null);
  const attach = vi.fn();
  await cleanDeviceFixture(fixture, "isolated-home", false, { attach });
  expect(rm).not.toHaveBeenCalled();
  expect(attach.mock.calls[0]?.[1].body).toContain("runtime cleanup failed");
  await expect(cleanDeviceFixture(fixture, "isolated-home", true, { attach })).rejects.toThrow(
    "code 1",
  );
});

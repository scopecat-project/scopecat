// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { projectEventStream } from "./project-event-stream";
class FakeSource extends EventTarget {
  static CLOSED = 2;
  static instances: FakeSource[] = [];
  readyState = 0;
  close = vi.fn();
  constructor(public url: string) {
    super();
    FakeSource.instances.push(this);
  }
}
beforeEach(() => {
  vi.useFakeTimers();
  FakeSource.instances = [];
  vi.stubGlobal("EventSource", FakeSource);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
it("leaves native connecting retries alone and replaces closed streams once with the current cursor", () => {
  let cursor = 1;
  const open = vi.fn();
  const project = vi.fn();
  const stop = projectEventStream(() => `/events?after=${cursor}`, open, project);
  const first = FakeSource.instances[0]!;
  first.dispatchEvent(new Event("error"));
  vi.advanceTimersByTime(9_000);
  expect(FakeSource.instances).toHaveLength(1);
  first.readyState = 2;
  first.dispatchEvent(new Event("error"));
  first.dispatchEvent(new Event("error"));
  cursor = 7;
  vi.advanceTimersByTime(2_999);
  expect(FakeSource.instances).toHaveLength(1);
  vi.advanceTimersByTime(1);
  expect(FakeSource.instances).toHaveLength(2);
  expect(first.close).toHaveBeenCalledTimes(1);
  const second = FakeSource.instances[1]!;
  expect(second.url).toBe("/events?after=7");
  first.dispatchEvent(new Event("open"));
  first.dispatchEvent(new Event("project"));
  expect(open).not.toHaveBeenCalled();
  expect(project).not.toHaveBeenCalled();
  second.dispatchEvent(new Event("open"));
  second.dispatchEvent(new Event("project"));
  expect(open).toHaveBeenCalledTimes(1);
  expect(project).toHaveBeenCalledTimes(1);
  second.readyState = 2;
  second.dispatchEvent(new Event("error"));
  stop();
  vi.advanceTimersByTime(30_000);
  expect(FakeSource.instances).toHaveLength(2);
  expect(second.close).toHaveBeenCalledTimes(1);
});

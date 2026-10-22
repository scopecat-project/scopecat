import { expect, it, vi } from "vitest";
import {
  launchCloseBlocked,
  prepareLaunchClose,
  releaseLaunchClose,
  retainLaunchCloseParticipant,
  type LaunchCloseStatus,
} from "./launch-close-barrier";

it("freezes synchronously, deduplicates requests and keeps the barrier until host release", async () => {
  let finish!: (status: LaunchCloseStatus) => void;
  const freeze = vi.fn();
  const flush = vi.fn(
    () =>
      new Promise<LaunchCloseStatus>((resolve) => {
        finish = resolve;
      }),
  );
  const detach = retainLaunchCloseParticipant({}, { freeze, flush, saved: () => true });
  const pending = prepareLaunchClose("close-1");
  expect(launchCloseBlocked()).toBe(true);
  expect(freeze).toHaveBeenCalledOnce();
  expect(prepareLaunchClose("close-1")).toBe(pending);
  finish("saved");
  await expect(pending).resolves.toBe("saved");
  expect(flush).toHaveBeenCalledOnce();
  expect(launchCloseBlocked()).toBe(true);
  releaseLaunchClose("unrelated");
  expect(launchCloseBlocked()).toBe(true);
  releaseLaunchClose("close-1");
  expect(launchCloseBlocked()).toBe(false);
  detach();
});

it("retains an unsettled participant after its provider leaves", async () => {
  let settled = false;
  const detach = retainLaunchCloseParticipant(
    {},
    { freeze() {}, flush: async () => (settled ? "saved" : "blocked"), saved: () => settled },
  );
  detach();
  await expect(prepareLaunchClose("blocked")).resolves.toBe("blocked");
  releaseLaunchClose("blocked");
  settled = true;
  await expect(prepareLaunchClose("retry")).resolves.toBe("saved");
  releaseLaunchClose("retry");
  detach();
});

it("never certifies a late flush after release or a changed participant set", async () => {
  let finish!: (status: LaunchCloseStatus) => void;
  const detach = retainLaunchCloseParticipant(
    {},
    {
      freeze() {},
      flush: () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
      saved: () => true,
    },
  );
  const first = prepareLaunchClose("expired");
  releaseLaunchClose("expired");
  finish("saved");
  await expect(first).resolves.toBe("unknown");
  const second = prepareLaunchClose("changed");
  const detachNew = retainLaunchCloseParticipant(
    {},
    { freeze() {}, flush: async () => "saved", saved: () => true },
  );
  finish("saved");
  await expect(second).resolves.toBe("unknown");
  releaseLaunchClose("changed");
  detach();
  detachNew();
});

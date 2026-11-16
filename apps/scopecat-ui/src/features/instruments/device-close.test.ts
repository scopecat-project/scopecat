import { expect, it, vi } from "vitest";
import { prepareDeviceClose, registerDeviceClose, releaseDeviceClose } from "./device-close";

it("releases a close wait without settling or duplicating the submitted operation", async () => {
  let finish!: (status: "saved") => void;
  let signal!: AbortSignal;
  const submitted = new Promise<"saved">((resolve) => {
    finish = resolve;
  });
  const controller = vi.fn((next: AbortSignal) => {
    signal = next;
    return submitted;
  });
  const unregister = registerDeviceClose(controller);
  const result = prepareDeviceClose("one");
  expect(prepareDeviceClose("one")).toBe(result);
  await Promise.resolve();
  releaseDeviceClose("one");
  await expect(result).resolves.toBe("unknown");
  expect(signal.aborted).toBe(true);
  expect(controller).toHaveBeenCalledTimes(1);
  finish("saved");
  await expect(submitted).resolves.toBe("saved");
  await expect(result).resolves.toBe("unknown");
  unregister();
});

it("does not start a controller after its close request has already been released", async () => {
  const controller = vi.fn(async () => "saved" as const);
  const unregister = registerDeviceClose(controller);
  const result = prepareDeviceClose("released");
  releaseDeviceClose("released");
  await expect(result).resolves.toBe("unknown");
  expect(controller).not.toHaveBeenCalled();
  unregister();
});

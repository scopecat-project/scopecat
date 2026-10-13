import { afterEach, expect, it, vi } from "vitest";
import { createRandomId } from "./random-id";
import { createConfigOperationId } from "../features/config/config-api";
import { createInstrumentCommandId } from "../features/instruments/instrument-api";

afterEach(() => vi.unstubAllGlobals());

it("uses randomUUID for both existing ID consumers", () => {
  const randomUUID = vi.fn(() => "random-identity");
  vi.stubGlobal("crypto", { randomUUID });
  expect(createConfigOperationId("template")).toBe("ui-config-template-random-identity");
  expect(createInstrumentCommandId("device")).toBe("ui-device-random-identity");
  expect(randomUUID).toHaveBeenCalledTimes(2);
});

it("uses 128 random bits when randomUUID is unavailable", () => {
  const getRandomValues = vi.fn((bytes: Uint8Array) => {
    expect(bytes.byteLength).toBe(16);
    bytes.fill(10);
    return bytes;
  });
  vi.stubGlobal("crypto", { getRandomValues });
  expect(createRandomId("connection")).toBe(`connection-${"0a".repeat(16)}`);
  expect(getRandomValues).toHaveBeenCalledOnce();
});

it("fails clearly if secure randomness is unavailable", () => {
  vi.stubGlobal("crypto", undefined);
  expect(() => createRandomId("device")).toThrow("Secure random IDs are unavailable");
});

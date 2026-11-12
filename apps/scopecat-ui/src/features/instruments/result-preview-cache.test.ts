import { expect, it } from "vitest";
import { ResultPreviewCache } from "./result-preview-cache";

it("evicts least recently used payload by byte budget without touching display state", () => {
  const cache = new ResultPreviewCache<string>(10);
  const display = new Map([["a", { representation: "phase", start: 100 }]]);
  cache.set("a", "a", 4);
  cache.set("b", "b", 4);
  expect(cache.get("a")).toBe("a");
  cache.set("c", "c", 4);
  expect(cache.get("b")).toBeUndefined();
  expect(cache.byteLength).toBe(8);
  cache.set("a", "large", 11);
  expect(cache.get("a")).toBeUndefined();
  expect(display.get("a")).toEqual({ representation: "phase", start: 100 });
  cache.clear();
  expect(cache.byteLength).toBe(0);
  expect(cache.size).toBe(0);
});
it("rejects invalid accounting and replaces entries without double counting", () => {
  const cache = new ResultPreviewCache<number>(10);
  for (const size of [0, -1, NaN, Infinity, 1.1]) cache.set("bad", 1, size);
  expect(cache.size).toBe(0);
  cache.set("a", 1, 8);
  cache.set("a", 2, 3);
  expect(cache.byteLength).toBe(3);
  expect(cache.get("a")).toBe(2);
});

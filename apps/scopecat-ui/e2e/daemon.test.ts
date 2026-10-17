// @vitest-environment node
import { expect, test } from "vitest";
import { daemon } from "./daemon";

// Use the same process entry as browser journeys, without starting a service or browser.
test.each(["start", "stop"])("browser daemon entry accepts %s", (command) => {
  expect(daemon([command, "--help"])).toContain("Usage:");
});

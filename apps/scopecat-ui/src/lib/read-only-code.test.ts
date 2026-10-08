import { execFileSync } from "node:child_process";
import { expect, it } from "vitest";
import { pythonStringLiteral, readOnlyCode } from "./read-only-code";

it("round-trips quotes, slashes, line breaks, all ASCII controls and Unicode through Python", () => {
  const value = `"'\\\n\r\t${Array.from({ length: 32 }, (_, i) => String.fromCharCode(i)).join("")}\x7f中文😀\u2028\u2029`;
  const literal = pythonStringLiteral(value);
  const actual = execFileSync(
    "uv",
    ["run", "--no-sync", "python", "-c", `import json; print(json.dumps(${literal}))`],
    { encoding: "utf8" },
  );
  expect(JSON.parse(actual)).toBe(value);
  expect(literal).not.toMatch(/[^\x20-\x7e]/);
});

it("opens exact IDs for both owners, preserving an older revision of the same key", () => {
  expect(readOnlyCode({ kind: "run", runId: "run-a", publicationId: "analysis-fit-r1" })).toContain(
    'run = session.run("run-a")\npublication = run.published_analysis("analysis-fit-r1")\npublication.outputs',
  );
  expect(readOnlyCode({ kind: "project", publicationId: "analysis-fit-r1" })).toContain(
    'publication = session.published_analysis("analysis-fit-r1")\npublication.outputs',
  );
  expect(readOnlyCode({ kind: "run", runId: "run-a" })).toMatch(
    /run = session.run\("run-a"\)\nrun$/,
  );
});

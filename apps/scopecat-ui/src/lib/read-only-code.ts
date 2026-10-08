export type PublicationCopyTarget = { kind: "run"; runId: string } | { kind: "project" };

export type ReadOnlyCopyTarget =
  | { kind: "run"; runId: string; publicationId?: string }
  | { kind: "project"; publicationId: string };

// ASCII-only Python literals also keep invisible/control characters out of cells.
export function pythonStringLiteral(value: string): string {
  let literal = '"';
  for (const character of value) {
    const point = character.codePointAt(0)!;
    if (character === '"' || character === "\\") literal += `\\${character}`;
    else if (point < 32 || point >= 127)
      literal +=
        point <= 0xffff
          ? `\\u${point.toString(16).padStart(4, "0")}`
          : `\\U${point.toString(16).padStart(8, "0")}`;
    else literal += character;
  }
  return `${literal}"`;
}

export function readOnlyCode(target: ReadOnlyCopyTarget): string {
  const lines = [
    "# First run your connection cell: session must be connected to this data space.",
    "# Paste into a separate cell and run only that cell, not Run All.",
    "# Read saved records only; custom typed/group objects are not reconstructed.",
  ];
  if (target.kind === "run") lines.push(`run = session.run(${pythonStringLiteral(target.runId)})`);
  if (target.publicationId !== undefined) {
    lines.push(
      `publication = ${target.kind === "run" ? "run" : "session"}.published_analysis(${pythonStringLiteral(target.publicationId)})`,
      "publication.outputs",
    );
  } else lines.push("run");
  return lines.join("\n");
}

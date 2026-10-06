// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { LaunchDraftProvider, useLaunchDraft } from "./LaunchDraft";
import type { LaunchCatalogEntry } from "./launch-api";
import { objectContext } from "../config/object-parameters.fixtures";

const context = objectContext();
const subject = context.resolution.context.subject;
if (subject.kind !== "registered_target") throw new Error("Expected registered target fixture");
const target = { kind: "registered_target" as const, ref: subject.ref };
const configuration = {
  kind: "parameters" as const,
  ref: context.resolution.branch!.revision,
  overrides: [],
};
const entry: LaunchCatalogEntry = {
  id: "signal",
  version: "1",
  title: "Signal",
  description: "Synthetic signal",
  actions: ["preview", "submit"],
  controls: [],
  request: { properties: {} },
  kind: "diagnostic",
  configuration_effect: "none",
};
function Harness() {
  const { draft, select, selectConfiguration, update } = useLaunchDraft();
  return (
    <>
      <button onClick={() => select(entry)}>Select experiment</button>
      <button
        onClick={() =>
          selectConfiguration(
            { ...configuration, setup: context.resolution.setup },
            { draft_id: "table-a", revision: 3 },
            target,
          )
        }
      >
        Adopt map context
      </button>
      <button
        onClick={() => selectConfiguration(configuration, { draft_id: "table-a", revision: 4 })}
      >
        Adopt newer parameters
      </button>
      <button
        onClick={() =>
          update((current) => ({
            ...current,
            selection: {
              ...current.selection,
              subject: { kind: "sample", sample_id: "other-chip" },
              configuration: {
                ...configuration,
                setup: { revision_id: "other-setup", content_hash: "sha256:other" },
              },
            },
            requestKey: "old-request",
            preview: {} as NonNullable<typeof current.preview>,
          }))
        }
      >
        Choose other context
      </button>
      <output data-testid="draft">{JSON.stringify(draft)}</output>
    </>
  );
}
function mount() {
  render(
    <LaunchDraftProvider projectId="project">
      <Harness />
    </LaunchDraftProvider>,
  );
}
function read() {
  return JSON.parse(screen.getByTestId("draft").textContent);
}
function click(name: string) {
  fireEvent.click(screen.getByRole("button", { name }));
}
afterEach(cleanup);

it("uses an explicitly adopted map context before the first experiment is selected", () => {
  mount();
  click("Adopt map context");
  click("Select experiment");
  expect(read().selection).toMatchObject({
    subject: target,
    configuration: { ...configuration, setup: context.resolution.setup },
  });
  expect(read().workingInput).toEqual({ draft_id: "table-a", revision: 3 });
  expect(read().preview).toBeUndefined();
});
it("replaces an existing context only on explicit context adoption and clears its preview", () => {
  mount();
  click("Select experiment");
  click("Choose other context");
  click("Adopt map context");
  expect(read().selection).toMatchObject({
    subject: target,
    configuration: { setup: context.resolution.setup },
  });
  expect(read().preview).toBeUndefined();
  expect(read().requestKey).toBeUndefined();
});
it("adopting newer parameters preserves the subsequently chosen target and setup", () => {
  mount();
  click("Adopt map context");
  click("Select experiment");
  click("Choose other context");
  click("Adopt newer parameters");
  expect(read().selection).toMatchObject({
    subject: { kind: "sample", sample_id: "other-chip" },
    configuration: { setup: { revision_id: "other-setup" } },
  });
  expect(read().workingInput.revision).toBe(4);
  expect(read().preview).toBeUndefined();
});

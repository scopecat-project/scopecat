// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DeviceContextPicker } from "./DeviceContextPicker";
import { ParameterBranchPicker } from "./ParameterBranchPicker";
import type { ScientificSelection } from "./scientific-selection";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function Draft({ name }: { name: string }) {
  const [value, setValue] = useState<ScientificSelection["configuration"]>({
    kind: "parameters",
    ref: { revision_id: "initial", content_hash: "sha256:initial" },
    overrides: [],
  });
  return (
    <section aria-label={name}>
      <ParameterBranchPicker value={value} projectId="shared" onChange={setValue} />
      {value.kind === "parameters" && (
        <DeviceContextPicker value={value} projectId="shared" onChange={setValue} />
      )}
      <output>{JSON.stringify(value)}</output>
    </section>
  );
}

it("keeps two drafts independent through refresh and parameter changes without connecting devices", async () => {
  let setups = [
    { id: "bench-a", content_hash: "sha256:a" },
    { id: "bench-b", content_hash: "sha256:b" },
  ];
  const calls: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      calls.push(request);
      const path = new URL(request.url).pathname;
      if (path.includes("/setup/resolutions/") || path.includes("/setup/revisions/")) {
        const id = path.split("/").at(-1)!;
        const setup = setups.find((item) => item.id === id)!;
        return Response.json({
          ...setup,
          resolution: { definition_id: id, definition_hash: "sha256:definition", devices: [] },
        });
      }
      return Response.json(
        new URL(request.url).pathname.endsWith("branches")
          ? {
              items: [
                {
                  name: "trial",
                  generation: 1,
                  actor: "author",
                  revision: { revision_id: "next", content_hash: "sha256:next" },
                },
              ],
              next_cursor: null,
            }
          : { items: setups },
      );
    }),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <Draft name="First draft" />
      <Draft name="Second draft" />
    </QueryClientProvider>,
  );
  const first = within(screen.getByRole("region", { name: "First draft" }));
  const second = within(screen.getByRole("region", { name: "Second draft" }));
  await first.findByRole("option", { name: "bench-a" });
  fireEvent.change(first.getByLabelText("Experiment setup"), { target: { value: "bench-a" } });
  await second.findByRole("option", { name: "bench-b" });
  fireEvent.change(second.getByLabelText("Experiment setup"), { target: { value: "bench-b" } });
  await waitFor(() => expect(first.getByLabelText("Experiment setup")).toHaveValue("bench-a"));
  await waitFor(() => expect(second.getByLabelText("Experiment setup")).toHaveValue("bench-b"));
  setups = [...setups, { id: "bench-c", content_hash: "sha256:c" }];
  fireEvent.click(first.getByRole("button", { name: "Refresh setups" }));
  await first.findByRole("option", { name: "bench-c" });
  fireEvent.click(first.getByRole("button", { name: "Choose parameter branch" }));
  await first.findByRole("option", { name: /trial/ });
  fireEvent.change(first.getByLabelText("Parameter branch"), { target: { value: "trial" } });
  fireEvent.click(first.getByRole("button", { name: "Use this parameter version" }));
  expect(first.getByRole("status")).toHaveTextContent('"revision_id":"next"');
  expect(first.getByLabelText("Experiment setup")).toHaveValue("bench-a");
  expect(second.getByRole("status")).toHaveTextContent('"revision_id":"initial"');
  expect(second.getByLabelText("Experiment setup")).toHaveValue("bench-b");
  expect(
    calls
      .filter((request) => request.method !== "GET")
      .map((request) => new URL(request.url).pathname),
  ).toEqual(["/api/v1/setup/resolutions/bench-a", "/api/v1/setup/resolutions/bench-b"]);
});

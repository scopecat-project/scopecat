// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SampleCapabilities } from "./SampleCapabilities";

vi.mock("../launch/CalibrationProfiles", () => ({
  CalibrationProfiles: ({ context }: { context: unknown }) => (
    <pre data-testid="context">{JSON.stringify(context)}</pre>
  ),
}));
const sample = {
  sample_id: "chip",
  revision: 2,
  role: "primary",
  display_name: "Chip",
  kind: "chip",
  content_hash: "sha256:sample",
};
const subject = {
  kind: "inline_samples",
  catalog_id: "pair",
  samples: [sample, { ...sample, sample_id: "partner", role: "partner" }],
};
const source = {
  kind: "parameter_revision",
  parameters: { revision_id: "p1", content_hash: "sha256:p" },
  setup: { revision_id: "s1", content_hash: "sha256:s" },
  content_hash: "sha256:config",
  overrides: [],
};
const binding = {
  subject,
  setup_content_hash: "sha256:s",
  config_content_hash: "sha256:config",
  scenario: null,
};
const snapshot = {
  run_id: "r1",
  samples: [sample],
  config_source: source,
  scientific_binding: binding,
};
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SampleCapabilities
        sampleId="chip"
        revision={2}
        runs={[
          { runId: "r1", experimentId: "readout", samples: [sample] },
          { runId: "r2", experimentId: "gate", samples: [sample] },
          { runId: "old", experimentId: "old revision", samples: [{ ...sample, revision: 1 }] },
        ]}
      />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText("Capability evidence for this sample revision"));
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("requires an explicit measurement and retains the whole joint subject", async () => {
  const fetcher = vi.fn(async () => Response.json({ snapshot }));
  vi.stubGlobal("fetch", fetcher);
  mount();
  expect(fetcher).not.toHaveBeenCalled();
  expect(screen.queryByRole("option", { name: /old revision/ })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Measurement context"), { target: { value: "r1" } });
  const rendered = await screen.findByTestId("context");
  expect(JSON.parse(rendered.textContent)).toEqual({
    parameters: source.parameters,
    subject,
    target_binding: null,
    setup_content_hash: "sha256:s",
    scenario: null,
  });
  expect(screen.getByText(/historical context/)).toBeInTheDocument();
});

it.each([
  { ...snapshot, config_source: null },
  { ...snapshot, config_source: { ...source, overrides: [{ parameter: "drive", value: 1 }] } },
  { ...snapshot, samples: [{ ...sample, revision: 1 }] },
])("does not infer an exact context from unsupported or mismatched data", async (value) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ snapshot: value })),
  );
  mount();
  fireEvent.change(screen.getByLabelText("Measurement context"), { target: { value: "r1" } });
  await screen.findByRole("alert");
  expect(screen.queryByTestId("context")).not.toBeInTheDocument();
});

it("clears the old context immediately when another measurement is selected and fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (new URL(request.url).pathname.endsWith("/r2")) throw new Error("unavailable");
      return Response.json({ snapshot });
    }),
  );
  mount();
  const select = screen.getByLabelText("Measurement context");
  fireEvent.change(select, { target: { value: "r1" } });
  await screen.findByTestId("context");
  fireEvent.change(select, { target: { value: "r2" } });
  expect(screen.queryByTestId("context")).not.toBeInTheDocument();
  expect(await screen.findByRole("alert")).toHaveTextContent("The local daemon did not respond.");
});

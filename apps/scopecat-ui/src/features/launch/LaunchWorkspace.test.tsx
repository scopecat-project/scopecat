// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { LaunchWorkspace } from "./LaunchWorkspace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const entry = {
  id: "rabi",
  title: "Rabi",
  description: "Configured pulse",
  request: {
    required: ["qubit", "amplitude_max"],
    properties: {
      qubit: { type: "string", title: "Qubit", enum: ["Q12"] },
      amplitude_max: { type: "number", title: "Amplitude", maximum: 0.9 },
    },
  },
};
function mount() {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <LaunchWorkspace />
    </QueryClientProvider>,
  );
}
it("previews a typed request and clears results after edits", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ calibrations: [entry] }))
    .mockResolvedValueOnce(Response.json({ configuration_writeback: false }));
  vi.stubGlobal("fetch", fetcher);
  mount();
  fireEvent.change(await screen.findByLabelText("Qubit"), { target: { value: "Q12" } });
  fireEvent.change(screen.getByLabelText("Amplitude"), { target: { value: "0.4" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  await screen.findByText("Preview ready");
  const request = fetcher.mock.calls[1]?.[0] as Request;
  expect(await request.json()).toEqual({
    action: "preview",
    experiment: "rabi",
    inputs: { qubit: "Q12", amplitude_max: 0.4 },
  });
  fireEvent.change(screen.getByLabelText("Amplitude"), { target: { value: "0.3" } });
  await waitFor(() => expect(screen.queryByText("Preview ready")).toBeNull());
});
it("shows a project without registered calibrations", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ calibrations: [] })));
  mount();
  expect(
    await screen.findByText("This project has no calibration preview provider."),
  ).toBeInTheDocument();
});
it("shows compilation failure without a successful preview", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(Response.json({ calibrations: [entry] }))
      .mockResolvedValueOnce(Response.json({ detail: "binding unavailable" }, { status: 422 })),
  );
  mount();
  fireEvent.change(await screen.findByLabelText("Qubit"), { target: { value: "Q12" } });
  fireEvent.change(screen.getByLabelText("Amplitude"), { target: { value: "0.4" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("binding unavailable");
  expect(screen.queryByText("Preview ready")).toBeNull();
});

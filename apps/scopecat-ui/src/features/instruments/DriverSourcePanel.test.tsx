// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../../api-client";
import { DriverSourcePanel } from "./DriverSourcePanel";
import { getDriverSource, updateDriverSource } from "./device-api";

vi.mock("./device-api", () => ({
  getDriverSource: vi.fn(),
  updateDriverSource: vi.fn(),
}));

const selection = {
  request: {
    operation_id: "previous",
    source_root: "/lab/source",
    actor: "maintainer",
    expected_previous: null,
  },
  factory: "lab.drivers:build",
  artifact_hash: "sha256:driver",
  code_revision: { content_hash: "sha256:source" },
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getDriverSource).mockResolvedValue({ active: selection });
});
afterEach(cleanup);

function show(sessionBusy = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <DriverSourcePanel daemonUnavailable={false} sessionBusy={sessionBusy} />
    </QueryClientProvider>,
  );
  return invalidate;
}

it("only activates on explicit submission and refreshes device and driver views", async () => {
  vi.mocked(updateDriverSource).mockImplementation(async (request) => ({ ...selection, request }));
  const invalidate = show();
  await screen.findByText("Active driver source: /lab/source");
  expect(updateDriverSource).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Driver source directory"), {
    target: { value: "/lab/new-source" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Update from source" }));
  await screen.findByRole("status");
  expect(updateDriverSource).toHaveBeenCalledWith({
    operation_id: expect.stringMatching(/^ui-driver-source-/),
    source_root: "/lab/new-source",
    expected_previous: "previous",
    actor: "local-operator",
  });
  for (const key of [
    "driver-source",
    "devices",
    "instruments",
    "instrument-drivers",
    "device-drivers",
  ]) {
    expect(invalidate).toHaveBeenCalledWith({ queryKey: [key] });
  }
});

it("keeps the selected source visible and retries an uncertain result with the same request", async () => {
  vi.mocked(updateDriverSource)
    .mockRejectedValueOnce(new ApiError("Connection lost"))
    .mockImplementation(async (request) => ({ ...selection, request }));
  show();
  await screen.findByText("Active driver source: /lab/source");
  const button = screen.getByRole("button", { name: "Update from source" });
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent("Connection lost");
  expect(screen.getByText("Active driver source: /lab/source")).toBeVisible();
  await waitFor(() => expect(button).toBeEnabled());
  expect(updateDriverSource).toHaveBeenCalledTimes(1);
  fireEvent.click(button);
  await screen.findByRole("status");
  expect(vi.mocked(updateDriverSource).mock.calls[1]).toEqual(
    vi.mocked(updateDriverSource).mock.calls[0],
  );
});

it("uses the refreshed selection on an explicit retry after a rejected update", async () => {
  vi.mocked(updateDriverSource)
    .mockImplementationOnce(async () => {
      vi.mocked(getDriverSource).mockResolvedValue({
        active: { ...selection, request: { ...selection.request, operation_id: "other-update" } },
      });
      throw new ApiError("Source changed; inspect and retry", 409);
    })
    .mockImplementation(async (request) => ({ ...selection, request }));
  show();
  await screen.findByText("Active driver source: /lab/source");
  const button = screen.getByRole("button", { name: "Update from source" });
  fireEvent.click(button);
  await screen.findByRole("alert");
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await screen.findByRole("status");
  expect(vi.mocked(updateDriverSource).mock.calls[1]?.[0].expected_previous).toBe("other-update");
});

it("requires the current manual session to be released", async () => {
  show(true);
  await screen.findByText("Active driver source: /lab/source");
  expect(screen.getByRole("button", { name: "Update from source" })).toBeDisabled();
  expect(updateDriverSource).not.toHaveBeenCalled();
});

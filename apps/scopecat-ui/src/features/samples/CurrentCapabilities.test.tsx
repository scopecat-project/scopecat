// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CurrentCapabilities } from "./CurrentCapabilities";

vi.mock("../launch/CalibrationProfiles", () => ({
  CalibrationProfiles: () => <p>Profile inspector</p>,
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("resolves explicit choices and clears the captured context before a failed refresh", async () => {
  const bodies: unknown[] = [];
  let fail = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "GET")
        return Response.json({ items: [{ id: "saved-setup", content_hash: "sha256:s" }] });
      bodies.push(await request.json());
      if (fail) throw new Error("offline");
      return Response.json({
        context: { parameters: { revision_id: "p1" }, scenario: null },
        branch: { name: "daily", generation: 3 },
        setup: { revision_id: "saved-setup" },
      });
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <CurrentCapabilities sampleId="chip" revision={2} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText("Capability evidence from a parameter branch"));
  expect(screen.getByText("Resolve capability context")).toBeDisabled();
  await screen.findByRole("option", { name: "saved-setup" });
  fireEvent.change(screen.getByLabelText("Parameter branch"), { target: { value: "daily" } });
  fireEvent.change(screen.getByLabelText("Setup for capability context"), {
    target: { value: "saved-setup" },
  });
  fireEvent.click(screen.getByText("Resolve capability context"));
  await screen.findByText("Profile inspector");
  expect(bodies).toEqual([
    {
      branch: "daily",
      setup: { revision_id: "saved-setup", content_hash: "sha256:s" },
      samples: [{ sample_id: "chip", revision: 2, role: "subject" }],
    },
  ]);
  expect(screen.getByText(/generation 3/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Setup for capability context"), {
    target: { value: "" },
  });
  expect(screen.queryByText("Profile inspector")).not.toBeInTheDocument();
  fail = true;
  fireEvent.click(screen.getByText("Resolve capability context"));
  await screen.findByRole("alert");
  expect(bodies[1]).toEqual({
    branch: "daily",
    setup: null,
    samples: [{ sample_id: "chip", revision: 2, role: "subject" }],
  });
  expect(screen.queryByText("Profile inspector")).not.toBeInTheDocument();
});

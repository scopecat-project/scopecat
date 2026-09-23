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

it("resolves one saved revision by name without a branch and clears stale results on lookup failure", async () => {
  const bodies: unknown[] = [];
  let fail = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      expect(request.method).toBe(path === "/api/v1/measurement-context/resolve" ? "POST" : "GET");
      if (path === "/api/v1/setup/revisions" || path === "/api/v1/measurement-targets")
        return Response.json({ items: [], next_cursor: null });
      if (path === "/api/v1/parameters/revisions/p1") {
        if (fail) return Response.json({ detail: "revision unavailable" }, { status: 404 });
        return Response.json({ id: "p1", content_hash: "sha256:p" });
      }
      expect(path).toBe("/api/v1/measurement-context/resolve");
      expect(request.method).toBe("POST");
      bodies.push(await request.json());
      return Response.json({
        context: { parameters: { revision_id: "p1" }, scenario: null },
        branch: null,
        setup: { revision_id: "active" },
      });
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <CurrentCapabilities sampleId="chip" revision={2} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText("Capability evidence from saved parameters"));
  fireEvent.change(screen.getByLabelText("Parameter source"), { target: { value: "revision" } });
  expect(screen.getByText("Resolve capability context")).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Saved parameter revision"), {
    target: { value: " p1 " },
  });
  fireEvent.click(screen.getByText("Resolve capability context"));
  await screen.findByText("Profile inspector");
  expect(screen.getByText(/Exact saved parameters p1/)).toBeInTheDocument();
  expect(bodies).toEqual([
    {
      parameters: { revision_id: "p1", content_hash: "sha256:p" },
      setup: null,
      samples: [{ sample_id: "chip", revision: 2, role: "subject" }],
      target: null,
    },
  ]);
  fireEvent.change(screen.getByLabelText("Parameter source"), { target: { value: "branch" } });
  expect(screen.queryByText("Profile inspector")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Parameter source"), { target: { value: "revision" } });
  fireEvent.click(screen.getByText("Resolve capability context"));
  await screen.findByText("Profile inspector");
  fail = true;
  fireEvent.click(screen.getByText("Resolve capability context"));
  await screen.findByRole("alert");
  expect(screen.queryByText("Profile inspector")).not.toBeInTheDocument();
  expect(bodies).toHaveLength(2);
});

it("resolves explicit choices and clears the captured context before a failed refresh", async () => {
  const bodies: unknown[] = [];
  let fail = false;
  const targetRef = {
    catalog_id: "catalog",
    target_id: "target",
    revision: 2,
    content_hash: "sha256:t",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (new URL(request.url).pathname.endsWith("measurement-targets"))
        return Response.json({
          items: [
            {
              ref: targetRef,
              name: "Registered chip",
              content: { members: [{ sample_id: "chip", revision: 2 }], connections: [] },
            },
          ],
          next_cursor: null,
        });
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
  fireEvent.click(screen.getByText("Capability evidence from saved parameters"));
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
      target: null,
    },
  ]);
  expect(screen.getByText(/generation 3/)).toBeInTheDocument();
  await screen.findByRole("option", { name: "Registered chip · target · r2" });
  fireEvent.change(screen.getByLabelText("Measurement subject"), { target: { value: "target:2" } });
  expect(screen.queryByText("Profile inspector")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("Resolve capability context"));
  await screen.findByText("Profile inspector");
  expect(bodies[1]).toEqual({
    branch: "daily",
    setup: { revision_id: "saved-setup", content_hash: "sha256:s" },
    samples: [],
    target: targetRef,
  });
  fireEvent.change(screen.getByLabelText("Setup for capability context"), {
    target: { value: "" },
  });
  expect(screen.queryByText("Profile inspector")).not.toBeInTheDocument();
  fail = true;
  fireEvent.click(screen.getByText("Resolve capability context"));
  await screen.findByRole("alert");
  expect(bodies[2]).toEqual({
    branch: "daily",
    setup: null,
    samples: [],
    target: targetRef,
  });
  expect(screen.queryByText("Profile inspector")).not.toBeInTheDocument();
});

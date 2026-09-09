// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { SampleArtifacts } from "./SampleArtifacts";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("opens only delivered or explicit external attachments and explains repair", async () => {
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(new URL(request.url).pathname);
      return Response.json({
        items: [
          {
            artifact: {
              id: "diagram",
              title: "Synthetic diagram",
              uri: "sha256:abc",
              media_type: "image/png",
            },
            status: "stored",
            url: "/api/v1/samples/sample-a/revisions/2/artifacts/content?artifact_id=diagram",
            reason: "Included in project snapshots.",
          },
          {
            artifact: {
              id: "manual",
              title: "Remote manual",
              uri: "https://example.com/manual",
              media_type: "application/pdf",
            },
            status: "external",
            url: "https://example.com/manual",
            reason: "External website; bytes are not included in snapshots.",
          },
          {
            artifact: {
              id: "missing",
              title: "Missing diagram",
              uri: "file:///outside/diagram.png",
            },
            status: "unavailable",
            url: null,
            reason: "Local file URIs are not delivered by the daemon.",
            repair:
              "Maintainer: import its bytes with lab.samples.import_artifact and write a new sample revision.",
          },
        ],
      });
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <SampleArtifacts sampleId="sample-a" revision={2} />
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("link", { name: "Open attachment" })).toHaveAttribute(
    "href",
    "/api/v1/samples/sample-a/revisions/2/artifacts/content?artifact_id=diagram",
  );
  expect(screen.getByRole("link", { name: "Open external website" })).toHaveAttribute(
    "rel",
    "noopener noreferrer",
  );
  expect(screen.getByText("Attachment unavailable")).toBeVisible();
  expect(screen.getByText(/Maintainer: import/)).toBeVisible();
  expect(screen.getAllByRole("link")).toHaveLength(2);
  expect(requests).toEqual(["/api/v1/samples/sample-a/revisions/2/artifacts"]);
  view.unmount();
  client.clear();
});
it("shows a connection repair message rather than turning unchecked URIs into links", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new TypeError("offline");
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <SampleArtifacts sampleId="sample-a" revision={1} />
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("Attachments could not be checked");
  expect(screen.queryByRole("link")).toBeNull();
  view.unmount();
  client.clear();
});

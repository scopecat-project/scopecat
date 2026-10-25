// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useState, type FormEvent } from "react";
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
  const submitted = vi.fn((event: FormEvent) => event.preventDefault());
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <form onSubmit={submitted}>
        <Draft name="First draft" />
        <Draft name="Second draft" />
      </form>
    </QueryClientProvider>,
  );
  const first = within(screen.getByRole("region", { name: "First draft" }));
  const second = within(screen.getByRole("region", { name: "Second draft" }));
  await first.findByRole("option", { name: "bench-a" });
  fireEvent.change(first.getByLabelText("实验配置"), { target: { value: "bench-a" } });
  await second.findByRole("option", { name: "bench-b" });
  fireEvent.change(second.getByLabelText("实验配置"), { target: { value: "bench-b" } });
  await waitFor(() => expect(first.getByLabelText("实验配置")).toHaveValue("bench-a"));
  await waitFor(() => expect(second.getByLabelText("实验配置")).toHaveValue("bench-b"));
  setups = [...setups, { id: "bench-c", content_hash: "sha256:c" }];
  fireEvent.click(first.getByRole("button", { name: "刷新实验配置" }));
  await first.findByRole("option", { name: "bench-c" });
  fireEvent.click(first.getByRole("button", { name: "选择参数分支" }));
  await first.findByRole("option", { name: /trial/ });
  fireEvent.change(first.getByLabelText("参数分支"), { target: { value: "trial" } });
  fireEvent.click(first.getByRole("button", { name: "使用此参数版本" }));
  expect(first.getByRole("status")).toHaveTextContent('"revision_id":"next"');
  expect(first.getByLabelText("实验配置")).toHaveValue("bench-a");
  expect(second.getByRole("status")).toHaveTextContent('"revision_id":"initial"');
  expect(second.getByLabelText("实验配置")).toHaveValue("bench-b");
  fireEvent.click(first.getByRole("button", { name: "重新检查设备连接" }));
  await waitFor(() => expect(calls.filter((request) => request.method !== "GET")).toHaveLength(3));
  expect(submitted).not.toHaveBeenCalled();
  expect(
    calls
      .filter((request) => request.method !== "GET")
      .map((request) => new URL(request.url).pathname),
  ).toEqual([
    "/api/v1/setup/resolutions/bench-a",
    "/api/v1/setup/resolutions/bench-b",
    "/api/v1/setup/resolutions/bench-a",
  ]);
});

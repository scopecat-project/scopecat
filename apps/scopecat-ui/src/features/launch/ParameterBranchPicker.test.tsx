// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ParameterBranchPicker } from "./ParameterBranchPicker";
import type { ScientificSelection } from "./scientific-selection";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const first = {
  name: "chip/daily",
  generation: 1,
  actor: "Alice",
  note: "initial calibration",
  revision: { revision_id: "first", content_hash: "sha256:first" },
};
function mount(onChange: (choice: ScientificSelection["configuration"]) => void) {
  function Editor() {
    const [value, setValue] = useState<ScientificSelection["configuration"]>({ kind: "active" });
    return (
      <ParameterBranchPicker
        projectId="lab"
        value={value}
        onChange={(choice) => {
          onChange(choice);
          setValue(choice);
        }}
      />
    );
  }
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <Editor />
    </QueryClientProvider>,
  );
}
it("pins the chosen version until explicit adoption of a newer branch head", async () => {
  let head = first;
  const fetcher = vi.fn(async () => Response.json({ items: [head], next_cursor: null }));
  vi.stubGlobal("fetch", fetcher);
  const changed = vi.fn();
  mount(changed);
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Choose parameter branch" }));
  await screen.findByRole("option", { name: /chip\/daily.*generation 1/ });
  fireEvent.change(screen.getByLabelText("Parameter branch"), { target: { value: first.name } });
  expect(changed).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Use this parameter version" }));
  expect(changed).toHaveBeenCalledWith({ kind: "parameters", ref: first.revision, overrides: [] });
  head = {
    ...first,
    generation: 2,
    revision: { revision_id: "second", content_hash: "sha256:second" },
  };
  fireEvent.click(screen.getByRole("button", { name: "Refresh parameter branches" }));
  await screen.findByRole("option", { name: /generation 2/ });
  expect(screen.getByText("Parameters: first")).toBeInTheDocument();
  expect(changed).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Use this parameter version" }));
  expect(changed).toHaveBeenLastCalledWith({
    kind: "parameters",
    ref: head.revision,
    overrides: [],
  });
  fireEvent.click(screen.getByRole("button", { name: "Use lab parameter default" }));
  expect(changed).toHaveBeenLastCalledWith({ kind: "active" });
});
it("pages branch choices and blocks adoption after a failed refresh", async () => {
  let fail = false;
  const fetcher = vi.fn(async (request: Request) => {
    if (fail) return Response.json({ detail: "Cannot read branches" }, { status: 503 });
    const after = new URL(request.url).searchParams.get("after");
    return Response.json(
      after
        ? { items: [{ ...first, name: "trial" }], next_cursor: null }
        : { items: [first], next_cursor: first.name },
    );
  });
  vi.stubGlobal("fetch", fetcher);
  const changed = vi.fn();
  mount(changed);
  fireEvent.click(screen.getByRole("button", { name: "Choose parameter branch" }));
  fireEvent.click(await screen.findByRole("button", { name: "Load more parameter branches" }));
  await screen.findByRole("option", { name: /trial/ });
  fireEvent.change(screen.getByLabelText("Parameter branch"), { target: { value: "trial" } });
  fail = true;
  fireEvent.click(screen.getByRole("button", { name: "Refresh parameter branches" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Cannot read branches");
  expect(screen.getByRole("button", { name: "Use this parameter version" })).toBeDisabled();
  expect(changed).not.toHaveBeenCalled();
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Refresh parameter branches" }));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Use this parameter version" })).toBeEnabled();
});

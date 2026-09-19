// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TargetPicker } from "./TargetPicker";
import { getTargetHeads, resolveTarget, targetRefKey, type TargetRevision } from "./target-api";
vi.mock("./target-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./target-api")>()),
  getTargetHeads: vi.fn(),
  resolveTarget: vi.fn(),
}));
const original: TargetRevision = {
  ref: { catalog_id: "catalog-A", target_id: "cooldown", revision: 1, content_hash: "sha256:one" },
  name: "Chip at low temperature",
  description: "",
  actor: "operator",
  note: "",
  recorded_at: "2026-09-19T00:00:00Z",
  content: {
    members: [{ id: "device", sample_id: "chip-A", revision: 4, content_hash: "sha256:sample" }],
    connections: [],
  },
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getTargetHeads).mockResolvedValue({ items: [original] });
  vi.mocked(resolveTarget).mockResolvedValue(original);
});
afterEach(cleanup);
function cache() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}
it("selects the complete catalog-qualified ref and retains it after the head advances", async () => {
  const onChange = vi.fn();
  const client = cache();
  const view = render(
    <QueryClientProvider client={client}>
      <TargetPicker projectId="page-A" browse onChange={onChange} />
    </QueryClientProvider>,
  );
  const option = await screen.findByRole("option", {
    name: /Chip at low temperature.*revision 1.*catalog catalog-A.*sample chip-A, revision 4/,
  });
  fireEvent.change(screen.getByLabelText("Registered target"), {
    target: { value: option.getAttribute("value") },
  });
  expect(onChange).toHaveBeenCalledWith(original.ref);
  view.rerender(
    <QueryClientProvider client={client}>
      <TargetPicker projectId="page-A" browse value={original.ref} onChange={onChange} />
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(resolveTarget).toHaveBeenCalledWith(original.ref, expect.any(AbortSignal)),
  );
  const advanced = {
    ...original,
    ref: { ...original.ref, revision: 2, content_hash: "sha256:two" },
  };
  vi.mocked(getTargetHeads).mockResolvedValue({ items: [advanced] });
  fireEvent.click(screen.getByRole("button", { name: "Refresh target list" }));
  await screen.findByRole("option", { name: /revision 2/ });
  expect(screen.getByLabelText("Registered target")).toHaveValue(targetRefKey(original.ref));
  expect(
    within(screen.getByLabelText("Selected registered target")).getByText(
      /revision 1.*catalog catalog-A/,
    ),
  ).toBeInTheDocument();
  expect(onChange).toHaveBeenCalledTimes(1);
});
it("resolves a retained foreign reference visibly without replacing it from catalog heads", async () => {
  vi.mocked(resolveTarget).mockRejectedValue(new Error("target belongs to another catalog"));
  const onChange = vi.fn();
  render(
    <QueryClientProvider client={cache()}>
      <TargetPicker
        projectId="other-project"
        browse={false}
        value={original.ref}
        onChange={onChange}
      />
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("target belongs to another catalog");
  expect(screen.getByText(/catalog catalog-A/)).toBeInTheDocument();
  expect(getTargetHeads).not.toHaveBeenCalled();
  expect(onChange).not.toHaveBeenCalled();
});
it("rejects multi-member selection and explains retained unsupported target plans", async () => {
  const multi = {
    ...original,
    content: {
      ...original.content,
      members: [
        ...original.content.members,
        { ...original.content.members[0]!, id: "second", sample_id: "chip-B" },
      ],
    },
  };
  vi.mocked(getTargetHeads).mockResolvedValue({ items: [multi] });
  vi.mocked(resolveTarget).mockResolvedValue(multi);
  const onChange = vi.fn();
  render(
    <QueryClientProvider client={cache()}>
      <TargetPicker browse value={multi.ref} onChange={onChange} />
    </QueryClientProvider>,
  );
  expect(
    await screen.findByRole("option", {
      name: /unsupported: Execution currently supports exactly one member/,
    }),
  ).toBeDisabled();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Execution currently supports exactly one member",
  );
  fireEvent.change(screen.getByLabelText("Registered target"), {
    target: { value: targetRefKey(multi.ref) },
  });
  expect(onChange).not.toHaveBeenCalled();
});
it("does not leak a different project's catalog cache", async () => {
  const client = cache();
  const onChange = vi.fn();
  const view = render(
    <QueryClientProvider client={client}>
      <TargetPicker browse projectId="A" onChange={onChange} />
    </QueryClientProvider>,
  );
  await screen.findByRole("option", { name: /catalog-A/ });
  vi.mocked(getTargetHeads).mockResolvedValue({ items: [] });
  view.rerender(
    <QueryClientProvider client={client}>
      <TargetPicker browse projectId="B" onChange={onChange} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("No registered targets in this catalog.")).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: /catalog-A/ })).not.toBeInTheDocument();
  act(() => {
    client.setQueryData(["target-catalog", "A"], {
      pages: [{ items: [original] }],
      pageParams: [undefined],
    });
  });
  expect(onChange).not.toHaveBeenCalled();
});

it("rejects single-member targets with connections", async () => {
  const connected: TargetRevision = {
    ...original,
    content: {
      ...original.content,
      connections: [
        {
          id: "loop",
          kind: "coupling",
          endpoints: [
            { member_id: "device", entity_id: "q0" },
            { member_id: "device", entity_id: "q1" },
          ],
        },
      ],
    },
  };
  vi.mocked(getTargetHeads).mockResolvedValue({ items: [connected] });
  vi.mocked(resolveTarget).mockResolvedValue(connected);
  render(
    <QueryClientProvider client={cache()}>
      <TargetPicker browse value={connected.ref} onChange={vi.fn()} />
    </QueryClientProvider>,
  );
  expect(
    await screen.findByRole("option", {
      name: /unsupported: Execution does not yet support target connections/,
    }),
  ).toBeDisabled();
  expect(await screen.findByRole("alert")).toHaveTextContent("target without connections");
});

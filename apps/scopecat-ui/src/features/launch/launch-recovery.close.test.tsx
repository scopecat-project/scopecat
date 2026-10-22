// @vitest-environment jsdom
import { useRef, useState } from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useLaunchRecovery, restoreDraft, type DraftRecord } from "./launch-recovery";
import { prepareLaunchClose, releaseLaunchClose } from "./launch-close-barrier";
import type { LaunchDraft } from "./LaunchDraft";
import { defaultSelection } from "./scientific-selection";

it("commits the synchronous input, flushes debounce and retains failed sessions across provider remount", async () => {
  const owner = crypto.randomUUID();
  let head: DraftRecord | null = null;
  let fail = false;
  const commands: Array<{ input: { values: Record<string, string> }; operation_id: string }> = [];
  vi.stubGlobal("fetch", async (request: Request) => {
    const command = await request.json();
    if (request.url.endsWith("/read")) return Response.json({ head });
    commands.push(command);
    if (fail) throw new Error("offline");
    head = {
      revision: (head?.revision ?? 0) + 1,
      target: command.target,
      input: command.input,
      state: "saved",
      created_at: "",
    };
    return Response.json({ head, saved: head });
  });
  function mount() {
    return renderHook(() => {
      const [draft, setDraft] = useState<LaunchDraft | undefined>(() =>
        head
          ? restoreDraft(head, 1)
          : {
              workspaceId: "author",
              experiment: "signal",
              definition: "{}",
              controlDefinition: "",
              values: { gain: "1" },
              controls: {},
              selection: defaultSelection(),
              actor: "tester",
              pending: false,
              error: "",
              notice: "",
              revision: 1,
            },
      );
      const current = useRef(draft);
      current.current = draft;
      const recovery = useLaunchRecovery(draft, setDraft, {
        owner,
        getDraft: () => current.current,
        revoke() {},
      });
      return {
        recovery,
        draft: draft!,
        change(value: string) {
          current.current = { ...current.current!, values: { gain: value } };
          setDraft(current.current);
        },
      };
    });
  }
  let view = mount();
  await waitFor(() => expect(view.result.current.recovery.ready).toBe(true));
  await act(async () => {
    view.result.current.change("2e");
    expect(await prepareLaunchClose("latest")).toBe("saved");
  });
  expect(commands.at(-1)?.input.values.gain).toBe("2e");
  releaseLaunchClose("latest");
  fail = true;
  act(() => view.result.current.change("3e"));
  await waitFor(() => expect(view.result.current.recovery.status).toContain("not confirmed saved"));
  const failedOperation = commands.at(-1)?.operation_id;
  view.unmount();
  await expect(prepareLaunchClose("detached")).resolves.toBe("blocked");
  releaseLaunchClose("detached");
  view = mount();
  await waitFor(() => expect(view.result.current.draft.values.gain).toBe("3e"));
  fail = false;
  const beforeRetry = commands.length;
  await act(async () => {
    await view.result.current.recovery.retry();
  });
  expect(commands[beforeRetry]?.operation_id).toBe(failedOperation);
  await act(async () => {
    expect(await prepareLaunchClose("recovered")).toBe("saved");
  });
  releaseLaunchClose("recovered");
  view.unmount();
  cleanup();
  vi.unstubAllGlobals();
});

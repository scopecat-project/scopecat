// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { clearDraftLocation, draftLocation, retainDraftLocation } from "./draft-location";

afterEach(() => window.history.replaceState(null, "", "/"));
it("retains monitor, preparation and unrelated context when replacing draft identity", () => {
  window.history.replaceState(null, "", "/?procedure=A&preparation=1&extra=keep#launch");
  retainDraftLocation({ workspaceId: "source & one", experiment: "signal", draftId: "B" });
  const url = new URL(window.location.href);
  expect(Object.fromEntries(url.searchParams)).toEqual({
    procedure: "A",
    preparation: "1",
    extra: "keep",
    workspace: "source & one",
    experiment: "signal",
    draft: "B",
  });
  expect(
    draftLocation({
      workspaceId: "source & one",
      experiment: "signal",
      draftId: "C",
    }).searchParams.get("draft"),
  ).toBe("C");
  clearDraftLocation("new-source");
  const cleared = new URL(window.location.href);
  expect(cleared.searchParams.has("draft")).toBe(false);
  expect(cleared.searchParams.has("experiment")).toBe(false);
  expect(cleared.searchParams.get("procedure")).toBe("A");
  expect(cleared.searchParams.get("workspace")).toBe("new-source");
});

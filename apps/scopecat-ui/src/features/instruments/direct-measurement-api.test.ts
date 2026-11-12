// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { discardDirectMeasurement, type MeasurementSave } from "./direct-measurement-api";
const selection: MeasurementSave = {
  session_id: "expired-session",
  instrument_id: "scope",
  command_id: "collect-one",
  line_id: "line",
  series_id: "series",
  title: "private title",
  line_label: "line",
  note: "note",
};
afterEach(() => vi.unstubAllGlobals());
it("discards using only the original collected-result identity", async () => {
  const fetch = vi.fn(async (request: Request) => {
    expect(request.method).toBe("POST");
    expect(new URL(request.url).pathname).toBe("/api/v1/direct-measurements/discard");
    expect(await request.json()).toEqual({
      session_id: "expired-session",
      instrument_id: "scope",
      command_id: "collect-one",
    });
    return Response.json({ status: "discarded", record_id: null });
  });
  vi.stubGlobal("fetch", fetch);
  await expect(discardDirectMeasurement(selection)).resolves.toEqual({
    status: "discarded",
    record_id: null,
  });
});
it.each([{ status: "unknown" }, { status: "saved", record_id: null }])(
  "does not confirm malformed disposition %j",
  async (receipt) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(receipt)),
    );
    await expect(discardDirectMeasurement(selection)).rejects.toThrow("原结果仍保留待核查");
  },
);
it("returns the existing saved identity without claiming discard", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ status: "saved", record_id: "existing" })),
  );
  await expect(discardDirectMeasurement(selection)).resolves.toEqual({
    status: "saved",
    record_id: "existing",
  });
});

it("returns exact not-retained evidence without treating it as a discarded result", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ status: "not_retained", record_id: null })),
  );
  await expect(discardDirectMeasurement(selection)).resolves.toEqual({
    status: "not_retained",
    record_id: null,
  });
});

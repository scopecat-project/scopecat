import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import type { InstrumentCollectReceipt } from "../../api-contract";

type ProducerReceipt = {
  content_base64: string;
  media_type: string;
  expected: InstrumentCollectReceipt;
};

// The real HTTP route and Python encoder supply these bytes; do not hand-frame
// successful responses here, or protocol drift can hide behind matching mocks.
export const producerReceipts: ProducerReceipt[] = JSON.parse(
  execFileSync(
    "uv",
    [
      "run",
      "--no-sync",
      "python",
      resolve("../../packages/scopecat-server/tests/fixtures/ui_collect_receipts.py"),
    ],
    { encoding: "utf8" },
  ),
);

export function producerContent(index = 0): ArrayBuffer {
  return new Uint8Array(Buffer.from(producerReceipts[index]!.content_base64, "base64")).buffer;
}

export function hardwareReceiptResponse(index = 0): Response {
  return new Response(producerContent(index), {
    status: 200,
    headers: { "Content-Type": producerReceipts[index]!.media_type },
  });
}

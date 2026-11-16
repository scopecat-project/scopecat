/** A UI wait budget, not a cancellation of a submitted daemon operation. */
export const INSTRUMENT_REQUEST_WAIT_MS = 15_000;

export class InstrumentRequestWaitExpired extends Error {
  constructor() {
    super("等待响应已超过 15 秒；请求可能仍在处理，结果尚未确认");
    this.name = "InstrumentRequestWaitExpired";
  }
}

export async function waitForInstrumentResponse<T>(request: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      request,
      new Promise<never>((_, reject) => {
        timer = setTimeout(
          () => reject(new InstrumentRequestWaitExpired()),
          INSTRUMENT_REQUEST_WAIT_MS,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

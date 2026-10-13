/** Opaque identity backed only by browser cryptographic randomness. */
export function createRandomId(prefix: string): string {
  const crypto = globalThis.crypto;
  if (typeof crypto?.randomUUID === "function") {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  if (typeof crypto?.getRandomValues === "function") {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    return `${prefix}-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
  }
  throw new Error(
    "Secure random IDs are unavailable. Open the application in a supported browser.",
  );
}

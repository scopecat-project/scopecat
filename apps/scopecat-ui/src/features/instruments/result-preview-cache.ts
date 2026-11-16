/** Bounded encoded-byte cache; presentation state deliberately lives elsewhere.
 * This is not a claim about exact JS heap size. API cell/text bounds also apply. */
export class ResultPreviewCache<T> {
  private entries = new Map<string, { value: T; bytes: number }>();
  private used = 0;
  constructor(readonly budgetBytes = 1024 * 1024) {}
  get byteLength() {
    return this.used;
  }
  get size() {
    return this.entries.size;
  }
  get(key: string): T | undefined {
    const entry = this.entries.get(key);
    if (!entry) return undefined;
    this.entries.delete(key);
    this.entries.set(key, entry);
    return entry.value;
  }
  set(key: string, value: T, bytes: number) {
    this.delete(key);
    if (!Number.isSafeInteger(bytes) || bytes <= 0 || bytes > this.budgetBytes) return;
    while (this.used + bytes > this.budgetBytes && this.entries.size) {
      this.delete(this.entries.keys().next().value!);
    }
    this.entries.set(key, { value, bytes });
    this.used += bytes;
  }
  delete(key: string) {
    const entry = this.entries.get(key);
    if (entry) this.used -= entry.bytes;
    this.entries.delete(key);
  }
  clear() {
    this.entries.clear();
    this.used = 0;
  }
}

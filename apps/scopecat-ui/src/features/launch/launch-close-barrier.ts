/** Window-owned protection for experiment input; this is not execution ownership. */
export type LaunchCloseStatus = "saved" | "blocked" | "unknown";
export interface LaunchCloseParticipant {
  freeze(): void;
  flush(): Promise<LaunchCloseStatus>;
  saved(): boolean;
}
const participants = new Map<object, LaunchCloseParticipant>();
const requests = new Map<string, Promise<LaunchCloseStatus>>();
let generation = 0;

export function launchCloseBlocked() {
  return requests.size > 0;
}

export function retainLaunchCloseParticipant(key: object, participant: LaunchCloseParticipant) {
  participants.set(key, participant);
  generation += 1;
  // A provider mounted during a pending close cannot introduce editable input.
  if (launchCloseBlocked()) participant.freeze();
  return () => {
    participant.freeze();
    if (participants.get(key) === participant && participant.saved()) {
      participants.delete(key);
      generation += 1;
    }
    // Unsettled sessions remain window-owned after their React provider leaves.
  };
}

export function prepareLaunchClose(requestId: string): Promise<LaunchCloseStatus> {
  const existing = requests.get(requestId);
  if (existing) return existing;
  let finish!: (status: LaunchCloseStatus) => void;
  const result = new Promise<LaunchCloseStatus>((resolve) => {
    finish = resolve;
  });
  requests.set(requestId, result); // Fence mutations synchronously, before any flush awaits.
  const selected = [...participants.values()];
  const observed = generation;
  try {
    for (const participant of selected) participant.freeze();
    void Promise.all(selected.map((participant) => participant.flush())).then(
      (statuses) => {
        if (!requests.has(requestId)) return finish("unknown");
        if (observed !== generation) return finish("unknown");
        if (statuses.includes("blocked")) return finish("blocked");
        if (statuses.includes("unknown") || selected.some((participant) => !participant.saved()))
          return finish("unknown");
        finish("saved");
      },
      () => finish("unknown"),
    );
  } catch {
    finish("unknown");
  }
  return result;
}

export function releaseLaunchClose(requestId: string) {
  requests.delete(requestId);
}

/** Test harness teardown models destruction of a browser window, not provider unmount. */
export function resetLaunchCloseWindowForTests() {
  participants.clear();
  requests.clear();
  generation += 1;
}

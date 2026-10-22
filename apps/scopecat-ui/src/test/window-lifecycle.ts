import { beforeEach } from "vitest";

// Each case represents a new browser window. Provider unmounts within a case
// deliberately retain unsettled launch sessions and must not reset this state.
beforeEach(async () => {
  if (typeof window === "undefined") return;
  const { resetLaunchRecoveryWindowForTests } = await import("../features/launch/launch-recovery");
  resetLaunchRecoveryWindowForTests();
});

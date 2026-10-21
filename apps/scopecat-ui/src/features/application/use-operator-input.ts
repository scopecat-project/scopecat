import { useCallback, useEffect, useRef, useState } from "react";
import { readOperatorSettings } from "./OperatorSettings";

// A form snapshots attribution once. Later Settings changes must not rewrite an
// explicit edit, recovered draft, or command whose outcome is uncertain.
export function useOperatorInput(retained?: string) {
  const [actor, setActorState] = useState(retained);
  const current = useRef(retained);
  const explicit = useRef(retained);
  const [overridden, setOverridden] = useState(retained !== undefined);
  const generation = useRef(0);
  const loading = useRef<Promise<string> | undefined>(undefined);
  const [error, setError] = useState<string>();
  const setActor = useCallback((value: string) => {
    explicit.current = value;
    setOverridden(true);
    current.current = value;
    setActorState(value);
    setError(undefined);
  }, []);
  const freshSnapshot = useCallback(async () => {
    if (explicit.current !== undefined) return explicit.current;
    const request = ++generation.current;
    const settings = await readOperatorSettings();
    if (explicit.current !== undefined)
      throw new Error(
        "Operator changed while loading attribution. Retry with the edited operator.",
      );
    if (request === generation.current) {
      current.current = settings.operator;
      setActorState(settings.operator);
      setError(undefined);
    }
    return settings.operator;
  }, []);
  const snapshot = useCallback(async () => {
    if (current.current !== undefined) return current.current;
    loading.current ??= freshSnapshot().finally(() => {
      loading.current = undefined;
    });
    return loading.current;
  }, [freshSnapshot]);
  useEffect(() => {
    void snapshot().catch((failure: unknown) => {
      if (explicit.current === undefined)
        setError(failure instanceof Error ? failure.message : String(failure));
    });
  }, [snapshot]);
  return {
    actor: actor ?? "",
    setActor,
    snapshot,
    freshSnapshot,
    overridden,
    error,
    ready: actor !== undefined,
  };
}

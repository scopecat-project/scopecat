import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ApiError } from "../../api-client";
import type { components } from "../../api-schema";
import { createInstrumentCommandId } from "./instrument-api";
import { finishInstrumentSession } from "./instrument-finish-api";
import { waitForInstrumentResponse } from "./instrument-request-wait";

type FinishCommand = components["schemas"]["InstrumentFinishCommand"];
export interface InstrumentFinishRecoveryState {
  command: FinishCommand;
  phase: "pending" | "unknown" | "rejected" | "confirmed";
  error?: string;
}

/** Only an identity-matched daemon rejection proves no finish selection was admitted. */
export function isUnadmittedFinish(error: unknown, sessionId: string, command: FinishCommand) {
  if (!(error instanceof ApiError) || error.status !== 409) return false;
  const detail = error.detail;
  return (
    typeof detail === "object" &&
    detail !== null &&
    "kind" in detail &&
    detail.kind === "instrument_finish_rejected" &&
    "session_id" in detail &&
    detail.session_id === sessionId &&
    "operation_id" in detail &&
    detail.operation_id === command.operation_id &&
    "message" in detail &&
    typeof detail.message === "string"
  );
}

/** Keep this hook in the inspector so dismissing a dialog cannot replace a submitted identity. */
export function useInstrumentFinishRecovery(sessionId?: string) {
  const [state, setState] = useState<InstrumentFinishRecoveryState>();
  const current = useRef<InstrumentFinishRecoveryState | undefined>(undefined);
  const session = useRef(sessionId);
  const attempt = useRef(0);
  const completion = useRef<(() => void) | undefined>(undefined);
  const update = (value: InstrumentFinishRecoveryState | undefined) => {
    current.current = value;
    setState(value);
  };
  useEffect(() => {
    if (session.current !== sessionId) {
      session.current = sessionId;
      attempt.current += 1;
      completion.current = undefined;
      update(undefined);
    }
  }, [sessionId]);
  useEffect(
    () => () => {
      completion.current = undefined;
    },
    [],
  );
  const finish = useMutation({
    mutationKey: ["device-control", sessionId],
    retry: false,
    mutationFn: async ({
      id,
      command,
      sequence,
    }: {
      id: string;
      command: FinishCommand;
      sequence: number;
    }) => {
      const request = finishInstrumentSession(id, command).then((receipt) => {
        if (receipt.session_id !== id || receipt.status !== "closed") {
          throw new Error("收尾回执与原会话或结束状态不匹配");
        }
        return receipt;
      });
      // A late success is still evidence. Cancellation only removes the exit callback.
      void request.then(
        () => {
          if (
            session.current !== id ||
            current.current?.command.operation_id !== command.operation_id ||
            current.current.phase === "confirmed"
          )
            return;
          update({ command, phase: "confirmed" });
          const callback = completion.current;
          completion.current = undefined;
          callback?.();
        },
        (error: unknown) => {
          if (
            session.current === id &&
            sequence === attempt.current &&
            current.current?.phase === "unknown" &&
            isUnadmittedFinish(error, id, command)
          ) {
            update({
              command,
              phase: "rejected",
              error: error instanceof Error ? error.message : String(error),
            });
          }
        },
      );
      try {
        await waitForInstrumentResponse(request);
      } catch (error) {
        if (
          session.current !== id ||
          sequence !== attempt.current ||
          current.current?.phase === "confirmed"
        )
          return;
        completion.current = undefined;
        update({
          command,
          phase: isUnadmittedFinish(error, id, command) ? "rejected" : "unknown",
          error: error instanceof Error ? error.message : String(error),
        });
      }
    },
  });
  return {
    state,
    start: (mode: FinishCommand["mode"], onFinished: () => void) => {
      if (
        !sessionId ||
        current.current?.phase === "pending" ||
        current.current?.phase === "confirmed"
      )
        return;
      const previous = current.current;
      if (previous && previous.phase !== "rejected" && previous.command.mode !== mode) return;
      const command =
        previous && previous.phase !== "rejected"
          ? previous.command
          : { operation_id: createInstrumentCommandId("finish"), mode };
      completion.current = onFinished;
      update({ command, phase: "pending" });
      finish.mutate({ id: sessionId, command, sequence: ++attempt.current });
    },
    cancelWaiting: () => {
      completion.current = undefined;
    },
  };
}

export type InstrumentFinishRecovery = ReturnType<typeof useInstrumentFinishRecovery>;

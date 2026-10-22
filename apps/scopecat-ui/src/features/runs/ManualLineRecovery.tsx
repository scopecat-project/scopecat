import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { ManualLineConfirmation, ManualLineReconfirmation } from "../../api-contract";
import { useOperatorInput } from "../application/use-operator-input";
import { errorMessage } from "../../lib/presentation";
import { fieldLabel, formControl } from "../../ui/styles";
import { reconfirmManualLine } from "./manual-line-recovery";

function retained(
  key: string,
  original: ManualLineConfirmation,
): ManualLineReconfirmation | undefined {
  const raw = localStorage.getItem(key);
  if (!raw) return undefined;
  const value: unknown = JSON.parse(raw);
  if (
    !value ||
    typeof value !== "object" ||
    !("actor" in value) ||
    !("request_key" in value) ||
    typeof value.actor !== "string" ||
    typeof value.request_key !== "string"
  ) {
    throw new Error(
      "The saved confirmation could not be read. Inspect this run before continuing.",
    );
  }
  return { original, actor: value.actor, request_key: value.request_key };
}

export function ManualLineRecovery({
  runId,
  fingerprint,
  original,
  configHash,
  pendingOnly = false,
}: {
  runId: string;
  fingerprint: string;
  original: ManualLineConfirmation;
  configHash?: string;
  pendingOnly?: boolean;
}) {
  const key = `scopecat.manual-line-confirmation:${runId}:${fingerprint}`;
  const [saved] = useState(() => {
    try {
      return { command: retained(key, original), error: "" };
    } catch (error) {
      return { command: undefined, error: errorMessage(error) };
    }
  });
  const [command, setCommand] = useState(saved.command);
  const operator = useOperatorInput(saved.command?.actor);
  const [checked, setChecked] = useState(false);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState(saved.error);
  const queryClient = useQueryClient();
  async function confirm() {
    if (pending || !checked || saved.error) return;
    setPending(true);
    try {
      const selected = command ?? {
        original,
        actor: await operator.freshSnapshot(),
        request_key: crypto.randomUUID(),
      };
      if (!selected.actor.trim()) throw new Error("Enter an operator.");
      // Persist before sending. An uncertain response must retain the actor and key.
      localStorage.setItem(
        key,
        JSON.stringify({ actor: selected.actor, request_key: selected.request_key }),
      );
      setCommand(selected);
      const result = await reconfirmManualLine(runId, fingerprint, selected);
      localStorage.removeItem(key);
      setCommand(undefined);
      setChecked(false);
      setMessage(
        result === "expired"
          ? "The earlier confirmation is retained but has expired after restart. Check the wiring and confirm again."
          : "Wiring confirmation recorded for the original run.",
      );
      await queryClient.invalidateQueries();
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setPending(false);
    }
  }
  if (pendingOnly && !command && !saved.error) return null;
  return (
    <section className="mt-3 grid gap-2" aria-label="Confirm current line wiring">
      <p>
        This measurement has not started. Confirm line <strong>{original.line_id}</strong> with its
        original configuration before continuing the same run.
      </p>
      <p className="break-all text-xs">
        Configuration: {configHash ?? "Inspect the retained configuration"}
      </p>
      <label className={fieldLabel}>
        Operator
        <input
          className={formControl}
          aria-label="Line confirmation operator"
          value={command?.actor ?? operator.actor}
          disabled={pending || !!command}
          onChange={(event) => operator.setActor(event.target.value)}
        />
      </label>
      <label>
        <input
          type="checkbox"
          checked={checked}
          disabled={pending}
          onChange={(event) => setChecked(event.target.checked)}
        />{" "}
        I checked the current wiring and this run’s configuration.
      </label>
      <button
        className="min-h-9 rounded-md border border-line-strong bg-panel px-3 py-1.5 text-sm text-text hover:bg-panel-strong disabled:opacity-50"
        type="button"
        disabled={pending || !checked || !operator.ready || !!saved.error}
        onClick={() => void confirm()}
      >
        {pending
          ? "Confirming…"
          : command
            ? "Retry saved confirmation"
            : "Confirm wiring and continue"}
      </button>
      {(message || operator.error) && <p role="status">{message || operator.error}</p>}
    </section>
  );
}

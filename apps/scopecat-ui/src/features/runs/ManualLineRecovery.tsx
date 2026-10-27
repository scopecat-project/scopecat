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
    throw new Error("无法读取已保存的确认。请先检查此运行，再继续。");
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
      if (!selected.actor.trim()) throw new Error("请输入操作者。");
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
          ? "先前确认仍被保留，但已在重启后失效。请核实接线并重新确认。"
          : "已为原运行记录接线确认。",
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
    <section className="mt-3 grid gap-2" aria-label="确认当前线路接线">
      <p>
        本次测量尚未开始。请核实线路 <strong>{original.line_id}</strong>{" "}
        的实际接线与原配置，再继续同一次运行。
      </p>
      <p className="break-all text-xs">配置：{configHash ?? "请检查已保留的配置"}</p>
      <label className={fieldLabel}>
        操作者
        <input
          className={formControl}
          aria-label="线路确认操作者"
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
        我已核实当前实际接线与本次运行的配置。
      </label>
      <button
        className="min-h-9 rounded-md border border-line-strong bg-panel px-3 py-1.5 text-sm text-text hover:bg-panel-strong disabled:opacity-50"
        type="button"
        disabled={pending || !checked || !operator.ready || !!saved.error}
        onClick={() => void confirm()}
      >
        {pending ? "正在确认…" : command ? "重试已保存的确认" : "确认接线并继续"}
      </button>
      {(message || operator.error) && <p role="status">{message || operator.error}</p>}
    </section>
  );
}

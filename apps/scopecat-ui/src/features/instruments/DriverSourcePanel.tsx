import { readOperatorSettings } from "../application/OperatorSettings";
import * as Controls from "../../ui/Controls";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api-client";
import { errorMessage } from "../../lib/presentation";
import { getDriverSource, updateDriverSource, type DriverSourceUpdate } from "./device-api";
import { createInstrumentCommandId } from "./instrument-api";

export function DriverSourcePanel({
  daemonUnavailable,
  sessionBusy,
}: {
  daemonUnavailable: boolean;
  sessionBusy: boolean;
}) {
  const client = useQueryClient();
  const [directory, setDirectory] = useState<string>();
  const [python, setPython] = useState("");
  const attempt = useRef<DriverSourceUpdate | undefined>(undefined);
  const source = useQuery({
    queryKey: ["driver-source"],
    queryFn: ({ signal }) => getDriverSource(signal),
    enabled: !daemonUnavailable,
  });
  const path = directory ?? source.data?.active?.request.source_root ?? "";
  const update = useMutation({
    retry: false,
    mutationFn: async () => {
      if (!attempt.current)
        attempt.current = {
          operation_id: createInstrumentCommandId("driver-source"),
          source_root: path.trim(),
          expected_previous: source.data?.active?.request.operation_id ?? null,
          actor: (await readOperatorSettings()).operator,
          ...(python.trim() ? { python: python.trim() } : {}),
        };
      return updateDriverSource(attempt.current);
    },
    onSuccess: () => {
      attempt.current = undefined;
    },
    onError: (error) => {
      // A rejected request can be resubmitted against the refreshed selection.
      // An uncertain transport result must reuse its original operation ID.
      if (error instanceof ApiError && error.status === 409) attempt.current = undefined;
    },
    onSettled: async () => {
      await Promise.all(
        ["driver-source", "devices", "instruments", "instrument-drivers", "device-drivers"].map(
          (key) => client.invalidateQueries({ queryKey: [key] }),
        ),
      );
    },
  });
  return (
    <form
      className="mt-4 grid gap-3 border-t border-line pt-3"
      aria-label="更新驱动源码"
      onSubmit={(event) => {
        event.preventDefault();
        update.mutate();
      }}
    >
      <p className="text-sm text-text-dim">
        可直接从源码目录调试驱动，无需重新安装应用。请先完成正在运行的实验并释放手动会话。更新会断开空闲设备，不会自动重新连接。
      </p>
      {source.data && (
        <p className="text-sm">
          {source.data.active
            ? `当前驱动源码：${source.data.active.request.source_root}`
            : "正在使用已安装驱动。"}
        </p>
      )}
      <Controls.Field className="grid gap-1">
        驱动源码目录
        <Controls.Input
          value={path}
          disabled={update.isPending}
          aria-describedby="driver-source-help"
          onChange={(event) => {
            setDirectory(event.target.value);
            attempt.current = undefined;
            update.reset();
          }}
        />
      </Controls.Field>
      <Controls.Field className="grid gap-1">
        驱动执行 Python（可选覆盖）
        <Controls.Input
          value={python}
          disabled={update.isPending}
          placeholder="使用源码目录已登记的执行环境"
          onChange={(event) => {
            setPython(event.target.value);
            attempt.current = undefined;
            update.reset();
          }}
        />
      </Controls.Field>
      {source.data?.active && (
        <p>
          当前 Python： <code>{source.data.active.python}</code>
        </p>
      )}
      <p id="driver-source-help" className="text-sm text-text-dim">
        使用应用所在计算机上的绝对路径，目录需包含
        scopecat.toml。实验和编译器修改会在准备实验时单独刷新。
      </p>
      {sessionBusy && <p>更新驱动前请释放手动会话。</p>}
      <Controls.Button
        type="submit"
        disabled={
          daemonUnavailable ||
          sessionBusy ||
          !path.trim() ||
          !source.isSuccess ||
          source.isFetching ||
          update.isPending
        }
      >
        {update.isPending ? "正在更新驱动…" : "从源码更新"}
      </Controls.Button>
      {(source.error || update.error) && (
        <p role="alert">{errorMessage(update.error ?? source.error)}</p>
      )}
      {update.isSuccess && <p role="status">驱动源码已更新。请重新连接设备并重新准备实验。</p>}
    </form>
  );
}

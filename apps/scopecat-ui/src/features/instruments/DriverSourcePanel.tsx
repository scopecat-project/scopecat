import { OperatorSettings, readOperatorSettings } from "../application/OperatorSettings";
import * as Controls from "../../ui/Controls";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api-client";
import { errorMessage } from "../../lib/presentation";
import { getDriverSource, updateDriverSource, type DriverSourceUpdate } from "./device-api";
import { createInstrumentCommandId } from "./instrument-api";

class MissingOperatorError extends Error {}

export function DriverSourcePanel({
  daemonUnavailable,
  sessionBusy,
  hasDrivers = false,
}: {
  daemonUnavailable: boolean;
  sessionBusy: boolean;
  hasDrivers?: boolean;
}) {
  const client = useQueryClient();
  const [directory, setDirectory] = useState<string>();
  const [needsOperator, setNeedsOperator] = useState(false);
  const [python, setPython] = useState("");
  const [native, setNative] = useState(window.pywebview?.api);
  useEffect(() => {
    const ready = () => setNative(window.pywebview?.api);
    window.addEventListener("pywebviewready", ready);
    return () => window.removeEventListener("pywebviewready", ready);
  }, []);
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
      if (!attempt.current) {
        const actor = (await readOperatorSettings()).operator;
        if (!actor.trim()) {
          setNeedsOperator(true);
          throw new MissingOperatorError(
            "启用驱动需要记录操作署名。请在下方保存默认操作者，再点击启用；所选目录会保留。",
          );
        }
        attempt.current = {
          operation_id: createInstrumentCommandId("driver-source"),
          source_root: path.trim(),
          expected_previous: source.data?.active?.request.operation_id ?? null,
          actor,
          ...(python.trim() ? { python: python.trim() } : {}),
        };
      }
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
    onSettled: async (_result, error) => {
      if (error instanceof MissingOperatorError) return;
      await Promise.all(
        ["driver-source", "devices", "instruments", "instrument-drivers", "device-drivers"].map(
          (key) => client.invalidateQueries({ queryKey: [key] }),
        ),
      );
    },
  });
  const picker = useMutation({
    mutationFn: async () => native?.choose_directory(),
    onSuccess: (chosen) => {
      if (chosen) {
        setDirectory(chosen);
        attempt.current = undefined;
        update.reset();
      }
    },
  });
  return (
    <>
      <form
        className="mt-4 grid gap-3 border-t border-line pt-3"
        aria-label="更新驱动源码"
        onSubmit={(event) => {
          event.preventDefault();
          if (!picker.isPending && !update.isPending) update.mutate();
        }}
      >
        <p className="text-sm text-text-dim">
          {hasDrivers
            ? "更换驱动来源会断开空闲设备；请先完成实验并释放手动会话。之后需要重新连接。"
            : "选择包含驱动的源码目录，检查并启用后即可添加设备，无需先创建实验 setup。"}
        </p>
        {source.data && (
          <p className="text-sm">
            {source.data.active
              ? `当前驱动源码：${source.data.active.request.source_root}`
              : hasDrivers
                ? "当前驱动由应用运行环境提供。"
                : "尚未启用驱动来源。"}
          </p>
        )}
        <Controls.Field className="grid gap-1">
          驱动源码目录
          <Controls.Input
            value={path}
            disabled={update.isPending || picker.isPending}
            aria-describedby="driver-source-help"
            onChange={(event) => {
              setDirectory(event.target.value);
              attempt.current = undefined;
              update.reset();
            }}
          />
        </Controls.Field>
        {native?.choose_directory && (
          <Controls.Button
            type="button"
            disabled={update.isPending || picker.isPending}
            onClick={() => {
              if (!picker.isPending && !update.isPending) picker.mutate();
            }}
          >
            {picker.isPending ? "正在选择…" : "选择源码目录"}
          </Controls.Button>
        )}
        {picker.error && <p role="alert">{errorMessage(picker.error)}</p>}
        <details>
          <summary>执行环境（高级）</summary>
          <Controls.Field className="grid gap-1">
            驱动执行 Python（可选覆盖）
            <Controls.Input
              value={python}
              disabled={update.isPending || picker.isPending}
              placeholder="使用源码目录已登记的执行环境"
              onChange={(event) => {
                setPython(event.target.value);
                attempt.current = undefined;
                update.reset();
              }}
            />
          </Controls.Field>
        </details>
        {source.data?.active && (
          <p>
            当前 Python： <code>{source.data.active.python}</code>
          </p>
        )}
        <p id="driver-source-help" className="text-sm text-text-dim">
          选择驱动项目的目录（含 scopecat.toml），而不是单个 Python 文件。
          应用会检查项目声明和执行环境，再启用其中的驱动；此步骤不会连接设备。
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
            update.isPending ||
            picker.isPending
          }
        >
          {update.isPending ? "正在检查并启用…" : hasDrivers ? "从源码更新" : "检查并启用驱动"}
        </Controls.Button>
        {(source.error || update.error) && (
          <p role="alert">{errorMessage(update.error ?? source.error)}</p>
        )}
        {update.isSuccess && <p role="status">驱动来源已启用。现在可以查看可用驱动并添加设备。</p>}
      </form>
      {needsOperator && (
        <div className="mt-3 grid gap-2">
          <OperatorSettings
            onSaved={() => {
              setNeedsOperator(false);
              update.reset();
            }}
          />
          <Controls.Button
            type="button"
            onClick={() => {
              setNeedsOperator(false);
              update.reset();
            }}
          >
            暂不设置，保留目录
          </Controls.Button>
        </div>
      )}
    </>
  );
}

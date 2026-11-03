import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api-client";
import { errorMessage } from "../../lib/presentation";
import * as Controls from "../../ui/Controls";
import { OperatorSettings, readOperatorSettings } from "../application/OperatorSettings";
import { DriverSourcePanel } from "./DriverSourcePanel";
import {
  getLocalDriverSources,
  manageLocalDriverSource,
  type DriverSourceCommand,
} from "./device-api";
import { createInstrumentCommandId } from "./instrument-api";

type Intent = Omit<DriverSourceCommand, "operation_id" | "expected_previous" | "actor">;

export function LocalDriverSourcesPanel(props: {
  daemonUnavailable: boolean;
  sessionBusy: boolean;
  hasDrivers?: boolean;
}) {
  const client = useQueryClient();
  const [path, setPath] = useState("");
  const [needsOperator, setNeedsOperator] = useState(false);
  const [native, setNative] = useState(window.pywebview?.api);
  const attempt = useRef<DriverSourceCommand | undefined>(undefined);
  const [uncertain, setUncertain] = useState(false);
  useEffect(() => {
    const ready = () => setNative(window.pywebview?.api);
    window.addEventListener("pywebviewready", ready);
    return () => window.removeEventListener("pywebviewready", ready);
  }, []);
  const sources = useQuery({
    queryKey: ["local-driver-sources"],
    queryFn: ({ signal }) => getLocalDriverSources(signal),
    enabled: !props.daemonUnavailable,
  });
  const update = useMutation({
    retry: false,
    mutationFn: async (intent: Intent) => {
      if (!attempt.current) {
        const actor = (await readOperatorSettings()).operator;
        if (!actor.trim()) {
          setNeedsOperator(true);
          throw new Error("请先保存操作署名，再启用来源。所选目录会保留。");
        }
        attempt.current = {
          ...intent,
          actor,
          operation_id: createInstrumentCommandId("driver-sources"),
          expected_previous: sources.data?.collection.operation_id ?? null,
        };
      }
      return manageLocalDriverSource(attempt.current);
    },
    onSuccess: () => {
      attempt.current = undefined;
      setUncertain(false);
    },
    onError: (error) => {
      if (
        error instanceof ApiError &&
        error.status !== undefined &&
        error.status >= 400 &&
        error.status < 500
      ) {
        attempt.current = undefined;
      }
      setUncertain(!!attempt.current);
    },
    onSettled: async () => {
      await Promise.all(
        [
          "local-driver-sources",
          "driver-source",
          "devices",
          "instruments",
          "instrument-drivers",
          "device-drivers",
        ].map((key) => client.invalidateQueries({ queryKey: [key] })),
      );
    },
  });
  const picker = useMutation({
    mutationFn: async () => native?.choose_directory(),
    onSuccess: (chosen) => {
      if (chosen) setPath(chosen);
    },
  });
  const blocked =
    props.daemonUnavailable ||
    props.sessionBusy ||
    !sources.isSuccess ||
    sources.isFetching ||
    update.isPending ||
    picker.isPending ||
    uncertain;
  const collection = sources.data?.collection;
  return (
    <section className="mt-4 grid gap-3 border-t border-line pt-3" aria-label="本地驱动来源">
      <p>
        添加来源不会连接设备。更新、启用或停用前请结束实验并释放手动会话；空闲设备连接会释放，之后可重新连接。
      </p>
      <p className="text-sm text-text-dim">
        本地项目需含 scopecat.toml，使用当前应用
        Python；请向实验室驱动维护者取得适用项目并准备依赖。来源不会自动安装软件。同名驱动冲突会明确拒绝。
      </p>
      {sources.data?.errors?.application && <p role="alert">{sources.data.errors.application}</p>}
      {sources.data?.unmanaged_backend ? (
        <>
          <p>当前驱动由应用配置提供。先通过下方控件保存其项目快照，再管理多个来源。</p>
          <DriverSourcePanel {...props} />
          <Controls.Button onClick={() => void sources.refetch()}>刷新来源状态</Controls.Button>
        </>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            {!collection?.sources.some((item) => item.builtin === "simulation") && (
              <Controls.Button
                disabled={blocked}
                onClick={() =>
                  update.mutate({
                    source_id: createInstrumentCommandId("simulation"),
                    builtin: "simulation",
                    action: "capture",
                  })
                }
              >
                启用模拟设备体验（无硬件）
              </Controls.Button>
            )}
            {!collection?.sources.some((item) => item.builtin === "public") && (
              <Controls.Button
                disabled={blocked}
                onClick={() =>
                  update.mutate({
                    source_id: createInstrumentCommandId("public"),
                    builtin: "public",
                    action: "capture",
                  })
                }
              >
                添加内置公共驱动
              </Controls.Button>
            )}
          </div>
          <p className="text-sm">
            模拟设备只产生合成响应，不代表真实测量。启用后在“添加设备”选择 Virtual RF
            source，连接后可改频率、读回并释放；不会替换真实驱动。
          </p>
          <form
            className="grid gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (!blocked && path.trim())
                update.mutate({
                  source_id: createInstrumentCommandId("local"),
                  source_root: path.trim(),
                  action: "capture",
                });
            }}
          >
            <Controls.Field>
              本地驱动项目目录
              <Controls.Input
                value={path}
                disabled={blocked}
                onChange={(event) => setPath(event.target.value)}
              />
            </Controls.Field>
            {native?.choose_directory && (
              <Controls.Button type="button" disabled={blocked} onClick={() => picker.mutate()}>
                选择项目目录
              </Controls.Button>
            )}
            <Controls.Button type="submit" disabled={blocked || !path.trim()}>
              添加本地来源
            </Controls.Button>
          </form>
          <ul className="grid gap-3">
            {collection?.sources.map((entry) => (
              <li key={entry.id} className="rounded border border-line p-3">
                <p>
                  {entry.builtin === "simulation"
                    ? "内置模拟设备（无硬件）"
                    : entry.builtin === "public"
                      ? "内置公共驱动"
                      : entry.selection.request.source_root}
                </p>
                <p className="text-sm">
                  来源：{entry.id} ·{" "}
                  {sources.data?.errors?.[entry.id]
                    ? "不可用"
                    : entry.enabled
                      ? "已启用"
                      : "已停用"}
                </p>
                <ul>
                  {entry.driver_ids.map((id) => (
                    <li key={id}>
                      <code>{id}</code>
                    </li>
                  ))}
                </ul>
                {sources.data?.errors?.[entry.id] && (
                  <p role="alert">{sources.data.errors?.[entry.id]}。请检查依赖后更新此来源。</p>
                )}
                <div className="flex gap-2">
                  <Controls.Button
                    disabled={blocked}
                    onClick={() =>
                      update.mutate({
                        source_id: entry.id,
                        action: entry.enabled ? "disable" : "enable",
                      })
                    }
                  >
                    {entry.enabled ? "停用来源" : "启用保留快照"}
                  </Controls.Button>
                  <Controls.Button
                    disabled={blocked}
                    onClick={() =>
                      update.mutate({
                        source_id: entry.id,
                        action: "capture",
                        ...(entry.builtin
                          ? { builtin: entry.builtin }
                          : { source_root: entry.selection.request.source_root }),
                      })
                    }
                  >
                    检查并更新来源
                  </Controls.Button>
                </div>
                <p className="text-sm text-text-dim">
                  停用前请先停用使用此来源的设备。停用保留快照和历史；其他来源不能接管这些驱动 ID。
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
      {needsOperator && (
        <OperatorSettings
          onSaved={() => {
            setNeedsOperator(false);
            update.reset();
          }}
        />
      )}
      {(sources.error || update.error || picker.error) && (
        <p role="alert">{errorMessage(update.error ?? sources.error ?? picker.error)}</p>
      )}
      {uncertain && (
        <Controls.Button
          disabled={update.isPending}
          onClick={() => {
            if (update.variables) update.mutate(update.variables);
          }}
        >
          核对并重试原操作
        </Controls.Button>
      )}
      {update.isPending && <p role="status">正在检查来源并等待维护围栏…</p>}
      {update.isSuccess && <p role="status">驱动来源已更新。可在上方目录查看驱动并添加设备。</p>}
    </section>
  );
}

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { Button, Field, Notice, Select } from "../../ui/Controls";
import { errorMessage } from "../../lib/presentation";

type Settings = {
  revision: number;
  storage_policy: "memory_only" | "allow_temporary_disk";
};

export function PendingResultSettings() {
  const client = useQueryClient();
  const settings = useQuery({
    queryKey: ["pending-result-settings"],
    queryFn: () => apiData(apiClient.GET("/api/v1/pending-result-settings")),
  });
  const capacity = useQuery({
    queryKey: ["pending-result-capacity"],
    queryFn: () => apiData(apiClient.GET("/api/v1/pending-result-capacity")),
    staleTime: Infinity,
    refetchOnMount: "always",
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  });
  const [edit, setEdit] = useState<Settings>();
  const save = useMutation({
    mutationFn: (command: Settings) =>
      apiData(apiClient.PUT("/api/v1/pending-result-settings", { body: command })),
    onSuccess: (value) => {
      client.setQueryData(["pending-result-settings"], value);
      setEdit(undefined);
      void capacity.refetch();
    },
  });
  return (
    <section className="grid min-w-0 gap-3 rounded-lg border border-line bg-panel p-4">
      <h3 className="font-semibold">待保存结果</h3>
      <p className="text-sm text-text-soft">
        此数据空间的设置跨窗口共享，仅影响之后被后台接纳的新采集。已有和正在采集的结果保持原策略，不会迁移或删除。
      </p>
      <p className="text-sm text-text-soft">
        两种方式都使用有界预览，保存仍保留完整原始回执；临时保留不等于正式保存，也不保证后台重启后恢复。
      </p>
      <section aria-label="后台载荷容量" className="grid gap-1 text-sm">
        <h4>当前后台容量</h4>
        <p>
          以下为应用载荷计费（已用 / 上限），不是进程总内存
          RSS，也不是系统可用磁盘。数值为上次读取的快照，不自动轮询。
        </p>
        {capacity.isPending && <p>正在读取容量…</p>}
        {capacity.data && (
          <>
            <p>
              内存载荷：{capacity.data.memory_bytes.toLocaleString()} /{" "}
              {capacity.data.memory_limit_bytes.toLocaleString()} bytes
            </p>
            <p>
              工作载荷：{capacity.data.work_bytes.toLocaleString()} /{" "}
              {capacity.data.work_limit_bytes.toLocaleString()} bytes
            </p>
            <p>
              临时磁盘载荷：{capacity.data.disk_bytes.toLocaleString()} /{" "}
              {capacity.data.disk_limit_bytes.toLocaleString()} bytes
            </p>
            {capacity.data.storage_degraded && (
              <p role="alert">
                临时存储状态异常；不要据此假定可以继续写盘。已有结果仍需保存或明确处置。
              </p>
            )}
          </>
        )}
        {capacity.error && (
          <p role="alert">
            容量读取失败：{errorMessage(capacity.error)}；已有快照可能已过时，不影响结果身份。
          </p>
        )}
        <Button disabled={capacity.isFetching} onClick={() => void capacity.refetch()}>
          刷新容量
        </Button>
      </section>
      {settings.isPending && <p>正在加载暂存设置…</p>}
      {settings.error && (
        <Notice role="alert">
          {errorMessage(settings.error)}
          <Button onClick={() => void settings.refetch()}>重新加载暂存设置</Button>
        </Notice>
      )}
      {settings.data && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (edit) save.mutate(edit);
          }}
        >
          <Field>
            未保存结果暂存方式
            <Select
              value={edit?.storage_policy ?? settings.data.storage_policy}
              disabled={save.isPending}
              onChange={(event) =>
                setEdit({
                  revision: edit?.revision ?? settings.data.revision,
                  storage_policy: event.target.value as Settings["storage_policy"],
                })
              }
            >
              <option value="memory_only">仅内存（默认）</option>
              <option value="allow_temporary_disk">允许临时磁盘</option>
            </Select>
          </Field>
          <p className="text-sm text-text-soft">
            仅内存：Scopecat
            不为未保存结果主动创建磁盘临时副本；容量不足时需先保存或明确放弃已有结果。这不控制操作系统的交换内存。
          </p>
          <p className="text-sm text-text-soft">
            允许临时磁盘：后台可以使用临时文件。切回仅内存后，之前已接纳的采集仍可能完成临时写盘，已有临时文件不会因此删除。
          </p>
          <Button type="submit" disabled={!edit || save.isPending}>
            保存暂存设置
          </Button>
        </form>
      )}
      {save.error && (
        <Notice role="alert">
          {errorMessage(save.error)}
          <Button
            onClick={() => {
              setEdit(undefined);
              save.reset();
              void settings.refetch();
            }}
          >
            重新加载暂存设置
          </Button>
        </Notice>
      )}
      {save.isSuccess && !edit && <p role="status">暂存设置已保存，仅对之后的新采集生效。</p>}
    </section>
  );
}

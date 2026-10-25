import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { Button, Field, Input, Notice } from "../../ui/Controls";
import { errorMessage } from "../../lib/presentation";

export function readOperatorSettings() {
  return apiData(apiClient.GET("/api/v1/operator-settings"));
}

export function OperatorSettings() {
  const settings = useQuery({ queryKey: ["operator-settings"], queryFn: readOperatorSettings });
  const client = useQueryClient();
  const [edit, setEdit] = useState<{ operator: string; revision: number }>();
  const save = useMutation({
    mutationFn: (command: { operator: string; revision: number }) =>
      apiData(apiClient.PUT("/api/v1/operator-settings", { body: command })),
    onSuccess: (value) => {
      client.setQueryData(["operator-settings"], value);
      setEdit(undefined);
    },
  });
  return (
    <section className="grid min-w-0 gap-3 rounded-lg border border-line bg-panel p-4">
      <h3 className="font-semibold">默认操作者</h3>
      <p className="text-sm text-text-soft">
        此数据空间中新操作表单的默认署名，跨窗口共享。已有草稿和重试保留原记录的操作者。此名称为可编辑署名，不是登录账号。
      </p>
      {settings.isPending && <p>正在加载操作者…</p>}
      {settings.error && <Notice role="alert">{errorMessage(settings.error)}</Notice>}
      {settings.data && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (edit) save.mutate(edit);
          }}
        >
          <Field>
            操作者名称
            <Input
              value={edit?.operator ?? settings.data.operator}
              disabled={save.isPending}
              onChange={(event) =>
                setEdit({
                  operator: event.target.value,
                  revision: edit?.revision ?? settings.data.revision,
                })
              }
            />
          </Field>
          <Button type="submit" disabled={!edit || save.isPending}>
            保存操作者
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
            重新加载操作者
          </Button>
        </Notice>
      )}
      {save.isSuccess && !edit && <p role="status">操作者已保存</p>}
    </section>
  );
}

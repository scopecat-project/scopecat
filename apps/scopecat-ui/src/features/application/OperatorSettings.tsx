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
      <h3 className="font-semibold">Default operator</h3>
      <p className="text-sm text-text-soft">
        Attribution for new Decision drafts in this data space, shared across windows. Existing
        drafts and retries keep their recorded operator. This name is editable attribution, not a
        login.
      </p>
      {settings.isPending && <p>Loading operator…</p>}
      {settings.error && <Notice role="alert">{errorMessage(settings.error)}</Notice>}
      {settings.data && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (edit) save.mutate(edit);
          }}
        >
          <Field>
            Operator name
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
            Save operator
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
            Reload operator
          </Button>
        </Notice>
      )}
      {save.isSuccess && !edit && <p role="status">Operator saved</p>}
    </section>
  );
}

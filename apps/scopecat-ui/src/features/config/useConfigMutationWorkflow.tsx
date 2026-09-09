import { useState, type ChangeEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ConfigProfileSnapshot, ConfigRegistryOverview } from "../../api-contract";
import { errorMessage } from "../../lib/presentation";
import { useConfirmationDialog } from "../../ui/ConfirmationDialog";
import {
  activateConfigEntry,
  createConfigOperationId,
  parseConfigProfileJson,
  publishConfig,
} from "./config-api";
import { safeConfigEntryId } from "./config-utils";

export type ConfigMutation =
  | { kind: "activate-entry"; entryId: string; expectedGeneration: number }
  | { kind: "undo"; entryId: string; expectedGeneration: number }
  | { kind: "import"; draft: ImportDraft };

export interface ImportDraft {
  fileName: string;
  entryId: string;
  config: ConfigProfileSnapshot;
}

export function useConfigMutationWorkflow(overview?: ConfigRegistryOverview) {
  const queryClient = useQueryClient();
  const [operator, setOperator] = useState("local-operator");
  const [note, setNote] = useState("");
  const [importDraft, setImportDraft] = useState<ImportDraft>();
  const [importError, setImportError] = useState<string>();
  const { requestConfirmation, confirmationDialog } = useConfirmationDialog();
  const generation = overview?.activation?.generation ?? 0;

  const mutation = useMutation({
    mutationFn: async (action: ConfigMutation) => {
      const command = {
        actor: operator.trim(),
        note: note.trim(),
        expected_generation: generation,
      };
      if (!command.actor) {
        throw new Error("Enter an operator name before changing configuration.");
      }
      switch (action.kind) {
        case "activate-entry":
          await activateConfigEntry({
            ...command,
            operation_id: createConfigOperationId("activate"),
            entry_id: action.entryId,
            expected_generation: action.expectedGeneration,
          });
          return;
        case "undo":
          await activateConfigEntry({
            ...command,
            operation_id: createConfigOperationId("undo"),
            entry_id: action.entryId,
            expected_generation: action.expectedGeneration,
          });
          return;
        case "import":
          await publishConfig({
            operation_id: createConfigOperationId("import"),
            source: {
              kind: "direct_config_profile",
              config: action.draft.config,
            },
            entry_id: action.draft.entryId,
            ...command,
          });
      }
    },
    onSuccess: async (_, action) => {
      if (action.kind === "import") setImportDraft(undefined);
      setNote("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["config"] }),
        queryClient.invalidateQueries({ queryKey: ["events"] }),
      ]);
    },
  });

  const runAction = (
    action: ConfigMutation,
    confirmation: string,
    confirmLabel = action.kind === "undo" ? "Restore default" : "Set as default",
  ) => {
    requestConfirmation({
      title: action.kind === "undo" ? "Restore the previous default?" : "Change the default?",
      description: confirmation,
      confirmLabel,
      onConfirm: () => mutation.mutate(action),
    });
  };
  const readImport = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    setImportError(undefined);
    try {
      const config = parseConfigProfileJson(await file.text());
      const suggestedEntryId =
        typeof config.id === "string" ? config.id : file.name.replace(/\.[^.]+$/, "");
      setImportDraft({
        fileName: file.name,
        entryId: safeConfigEntryId(suggestedEntryId),
        config,
      });
    } catch (error) {
      setImportError(errorMessage(error));
    }
  };

  return {
    operator,
    setOperator,
    note,
    setNote,
    importDraft,
    setImportDraft,
    importError,
    mutation,
    commandDisabled: mutation.isPending || !operator.trim(),
    runAction,
    readImport,
    dismissError: () => {
      setImportError(undefined);
      mutation.reset();
    },
    confirmationDialog,
  };
}

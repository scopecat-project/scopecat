import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { ParameterProposal } from "../../data/parameter-proposals/types";
import { navigate, navigateLink } from "../../lib/navigation";
import { Button } from "../../ui/Controls";
import { useOptionalLaunchDraft } from "../launch/LaunchDraft";
import { ExperimentPicker } from "../launch/ExperimentPicker";
import { SourceSelector } from "../launch/SourceSelector";
import { useAuthorWorkspaces } from "../launch/source-api";
import { draftLocation } from "../launch/draft-location";
import { normalizeSelection } from "../launch/scientific-selection";

export function CandidateLaunch({
  proposal,
  projectId,
}: {
  proposal: ParameterProposal;
  projectId: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="px-3.5 py-3 text-sm" aria-label="使用候选准备实验">
      <Button onClick={() => setOpen(true)} disabled={open}>
        用于新实验
      </Button>
      {open && (
        <CandidatePreparation
          key={`${projectId}:${proposal.sourceRunId}:${proposal.id}`}
          proposal={proposal}
          projectId={projectId}
          onCancel={() => setOpen(false)}
        />
      )}
    </section>
  );
}

function CandidatePreparation({
  proposal,
  projectId,
  onCancel,
}: {
  proposal: ParameterProposal;
  projectId: string;
  onCancel: () => void;
}) {
  const editor = useOptionalLaunchDraft();
  const sources = useAuthorWorkspaces(projectId);
  const [workspace, setWorkspace] = useState(editor?.workspaceId ?? "");
  const [experiment, setExperiment] = useState("");
  const [busy, setBusy] = useState(false);
  const saving = useRef(false);
  const [error, setError] = useState<string>();
  const [savedLink, setSavedLink] = useState<string>();
  const available = sources.data?.items.filter((item) => item.available) ?? [];
  const workspaceId = workspace || (available.length === 1 ? available[0]!.id : "");
  const catalog = useQuery({
    queryKey: ["candidate-launch-catalog", projectId, workspaceId],
    enabled: available.some((item) => item.id === workspaceId),
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/experiment-launcher", {
          params: { header: { "X-Scopecat-Workspace": workspaceId } },
          signal,
        }),
      ),
  });
  const selection = useQuery({
    queryKey: [
      "candidate-launch-selection",
      projectId,
      proposal.sourceRunId,
      proposal.id,
      proposal.analysisRecordId,
      proposal.baseContentHash,
    ],
    retry: false,
    queryFn: async ({ signal }) => {
      const result = await apiData(
        apiClient.GET("/api/v1/runs/{run_id}/parameter-proposals/{proposal_id}/launch-selection", {
          params: { path: { run_id: proposal.sourceRunId, proposal_id: proposal.id } },
          signal,
        }),
      );
      const configuration = result.configuration;
      if (
        configuration?.kind !== "candidate" ||
        configuration.source.source_run_id !== proposal.sourceRunId ||
        configuration.source.proposal_id !== proposal.id ||
        configuration.source.analysis_record_id !== proposal.analysisRecordId ||
        configuration.source.base_config_content_hash !== proposal.baseContentHash
      )
        throw new Error("候选来源与当前卡片不一致，请重新打开原候选。未替换为其他参数。");
      return normalizeSelection(result);
    },
  });
  const entry = catalog.data?.entries.find((item) => item.id === experiment);
  return (
    <div className="mt-3 space-y-3">
      <p>
        使用此候选的精确参数、原样品修订和批次创建独立草稿。选择实验后检查输入；这里不会启动实验、发布参数或标记验证通过。
      </p>
      <fieldset disabled={busy || Boolean(savedLink)} className="space-y-3">
        <SourceSelector
          catalog={sources}
          workspaceId={workspaceId}
          onSelect={(id) => {
            setWorkspace(id);
            setExperiment("");
          }}
        />
        {catalog.data && (
          <ExperimentPicker
            entries={catalog.data.entries}
            selectedId={experiment}
            disabled={busy}
            onSelect={(item) => setExperiment(item.id)}
          />
        )}
      </fieldset>
      {(selection.isPending || catalog.isFetching) && <p role="status">正在读取实验与候选…</p>}
      {(selection.error || catalog.error || error) && (
        <p role="alert">{error ?? selection.error?.message ?? catalog.error?.message}</p>
      )}
      {!editor && <p role="alert">实验编辑器不可用。</p>}
      <Button
        disabled={
          !editor ||
          !entry ||
          !selection.data ||
          Boolean(selection.error) ||
          catalog.isFetching ||
          !available.some((item) => item.id === workspaceId) ||
          busy ||
          Boolean(savedLink) ||
          Boolean(editor.draft?.experiment && !editor.recovery.ready) ||
          editor.recovery.writing
        }
        onClick={async () => {
          if (!editor || !entry || !selection.data || saving.current) return;
          const configuration = selection.data.configuration;
          if (configuration.kind !== "candidate") return;
          saving.current = true;
          setBusy(true);
          setError(undefined);
          const origin = window.location.href;
          try {
            const result = await editor.copyHistoricalConfiguration(
              proposal.sourceRunId,
              configuration.source,
              {
                entry,
                workspaceId,
                selection: selection.data,
                codeRevision: catalog.data?.code_revision ?? undefined,
                isIntended: () => window.location.href === origin,
              },
            );
            const url = draftLocation({
              workspaceId: result.target.workspace_id,
              experiment: result.target.experiment,
              draftId: result.target.draft_id,
            });
            url.hash = "launch";
            url.searchParams.set("preparation", "1");
            url.searchParams.delete("workbench");
            setSavedLink(url.pathname + url.search + url.hash);
            // Saving a draft must not pull the user back from another route.
            const current = new URL(window.location.href);
            const previous = new URL(origin);
            if (
              result.selected &&
              current.hash === previous.hash &&
              current.searchParams.get("run") === previous.searchParams.get("run")
            )
              navigate(url);
          } catch (failure) {
            setError(failure instanceof Error ? failure.message : String(failure));
          } finally {
            saving.current = false;
            setBusy(false);
          }
        }}
      >
        {busy ? "正在保存候选草稿…" : "创建候选草稿"}
      </Button>
      <Button disabled={busy} onClick={onCancel}>
        取消返回
      </Button>
      {savedLink && (
        <p>
          候选草稿已保存。
          <a href={savedLink} onClick={navigateLink} className="underline">
            打开候选草稿
          </a>
        </p>
      )}
    </div>
  );
}

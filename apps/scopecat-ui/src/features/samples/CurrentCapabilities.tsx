import * as Controls from "../../ui/Controls";
import { objectContextError, type ObjectParameterContext } from "../config/object-parameters";
import type { SampleView } from "../../api-contract";
import { useState } from "react";
import { contextParameterLabel } from "../launch/context-parameters";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { MethodResponse } from "openapi-fetch";
import { apiClient, apiData } from "../../api-client";
import { CalibrationProfiles } from "../launch/CalibrationProfiles";
import { getSetupDefinitions, resolveSetupDefinition } from "../config/setup-api";

type Resolution = MethodResponse<typeof apiClient, "post", "/api/v1/measurement-context/resolve">;

export function CurrentCapabilities({
  sampleId,
  revision,
  sample,
  onEditParameters,
}: {
  sampleId: string;
  revision: number;
  sample?: SampleView["revision"];
  onEditParameters?: (context: ObjectParameterContext) => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [branch, setBranch] = useState("");
  const [parameterId, setParameterId] = useState("");
  const [source, setSource] = useState<"branch" | "revision">("branch");
  const [setupId, setSetupId] = useState("");
  const [targetId, setTargetId] = useState("");
  const [resolution, setResolution] = useState<Resolution>();
  const [attempt, setAttempt] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [opening, setOpening] = useState(false);
  const setups = useQuery({
    queryKey: ["capability-setup-choices"],
    enabled: expanded,
    queryFn: ({ signal }) => getSetupDefinitions(signal),
  });
  const selectedSetup = setups.data?.items.find((item) => item.id === setupId);
  const targets = useInfiniteQuery({
    queryKey: ["capability-target-choices", sampleId, revision],
    enabled: expanded,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ signal, pageParam }) =>
      apiData(
        apiClient.GET("/api/v1/measurement-targets", {
          params: { query: { limit: 50, before: pageParam } },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const choices =
    targets.data?.pages
      .flatMap((page) => page.items)
      .filter(
        (item) =>
          item.content.members.length === 1 &&
          item.content.connections.length === 0 &&
          item.content.members[0]?.sample_id === sampleId &&
          item.content.members[0]?.revision === revision,
      ) ?? [];
  const selectedTarget = choices.find(
    (item) => `${item.ref.target_id}:${item.ref.revision}` === targetId,
  );
  async function resolve() {
    setAttempt((value) => value + 1);
    setPending(true);
    setError("");
    setResolution(undefined);
    try {
      if (!selectedSetup) throw new Error("请选择实验 setup。");
      const setup = await resolveSetupDefinition(selectedSetup.id);
      const parameters =
        source === "revision"
          ? await apiData(
              apiClient.GET("/api/v1/parameters/revisions/{revision_id}", {
                params: { path: { revision_id: parameterId.trim() } },
              }),
            )
          : undefined;
      setResolution(
        await apiData(
          apiClient.POST("/api/v1/measurement-context/resolve", {
            body: {
              ...(parameters
                ? {
                    parameters: {
                      revision_id: parameters.id,
                      content_hash: parameters.content_hash,
                    },
                  }
                : { branch: branch.trim() }),
              setup: { revision_id: setup.id, content_hash: setup.content_hash },
              samples: selectedTarget ? [] : [{ sample_id: sampleId, revision, role: "subject" }],
              target: selectedTarget?.ref ?? null,
            },
          }),
        ),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  return (
    <details
      id="sample-parameter-context"
      className="border border-line rounded-lg p-3 space-y-3 min-w-0 break-words"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>工作参数与能力上下文</summary>
      <p>
        为此精确样品版本选择参数分支或已保存版本及
        setup。解析会捕获当前版本，不运行实验或修改默认值。此入口选择 subject
        角色中的一个内联样品，或已登记的单成员目标。当前目标投影不支持连接目标。
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void resolve();
        }}
      >
        <fieldset disabled={pending} className="grid gap-3">
          <Controls.Field>
            测量对象
            <Controls.Select
              value={targetId}
              onChange={(event) => {
                setTargetId(event.target.value);
                setResolution(undefined);
                setError("");
              }}
            >
              <option value="">内联样品 · subject 角色</option>
              {choices.map((item) => (
                <option
                  key={`${item.ref.target_id}:${item.ref.revision}`}
                  value={`${item.ref.target_id}:${item.ref.revision}`}
                >
                  {item.name} · {item.ref.target_id} · r{item.ref.revision}
                </option>
              ))}
            </Controls.Select>
          </Controls.Field>
          {targets.hasNextPage && (
            <Controls.Button
              type="button"
              disabled={targets.isFetchingNextPage}
              onClick={() => {
                void targets.fetchNextPage();
              }}
            >
              加载更多目标选项
            </Controls.Button>
          )}
          <Controls.Field>
            参数来源
            <Controls.Select
              value={source}
              onChange={(event) => {
                setSource(event.target.value === "revision" ? "revision" : "branch");
                setResolution(undefined);
                setError("");
              }}
            >
              <option value="branch">参数分支</option>
              <option value="revision">精确保存版本</option>
            </Controls.Select>
          </Controls.Field>
          <Controls.Field>
            {source === "branch" ? "参数分支" : "已保存的参数版本"}
            <Controls.Input
              required
              value={source === "branch" ? branch : parameterId}
              onChange={(event) => {
                if (source === "branch") setBranch(event.target.value);
                else setParameterId(event.target.value);
                setResolution(undefined);
                setError("");
              }}
            />
          </Controls.Field>
          <Controls.Field>
            能力上下文的 setup
            <Controls.Select
              value={setupId}
              onChange={(event) => {
                setSetupId(event.target.value);
                setResolution(undefined);
                setError("");
              }}
            >
              <option value="">选择实验角色与设备</option>
              {setups.data?.items.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id}
                </option>
              ))}
            </Controls.Select>
          </Controls.Field>
          <Controls.Button
            type="submit"
            disabled={
              !(source === "branch" ? branch.trim() : parameterId.trim()) ||
              pending ||
              !selectedSetup ||
              Boolean(targetId && !selectedTarget)
            }
          >
            解析能力上下文
          </Controls.Button>
        </fieldset>
      </form>
      {setups.error && <p role="alert">无法加载已保存的 setup： {setups.error.message}.</p>}
      {targets.error && <p role="alert">无法加载已登记目标： {targets.error.message}.</p>}
      {pending && <p role="status">正在解析测量上下文…</p>}
      {error && <p role="alert">{error}</p>}
      {resolution && (
        <section key={attempt} aria-label="已解析的当前能力上下文">
          <p>
            {resolution.branch
              ? `分支 ${resolution.branch.name} · generation ${resolution.branch.generation} · `
              : "精确保存的"}
            参数 {contextParameterLabel(resolution.context.parameters)} · setup{" "}
            {resolution.setup?.revision_id ?? resolution.context.setup_content_hash}
          </p>
          <p>这些版本已固定用于此报告。需捕获所选分支或设备连接变化时，请重新解析。</p>
          <p>场景： {resolution.context.scenario?.id ?? "physical"}</p>
          <details>
            <summary>已解析的测量对象</summary>
            <pre>{JSON.stringify(resolution.context.subject, null, 2)}</pre>
          </details>
          {sample && onEditParameters && (
            <div className="grid gap-2 rounded border border-line p-3">
              <p>在样品地图旁编辑此分支的工作参数。此操作不会将输入用于实验。</p>
              {objectContextError({ sample, resolution }) ? (
                <p>{objectContextError({ sample, resolution })}</p>
              ) : (
                <Controls.Button
                  disabled={opening}
                  onClick={async () => {
                    setOpening(true);
                    setError("");
                    try {
                      await onEditParameters({ sample, resolution, setupName: selectedSetup?.id });
                    } catch (caught) {
                      setError(caught instanceof Error ? caught.message : String(caught));
                    } finally {
                      setOpening(false);
                    }
                  }}
                >
                  在地图旁编辑工作参数
                </Controls.Button>
              )}
            </div>
          )}
          <CalibrationProfiles
            context={resolution.context}
            contextDescription="使用明确解析出的参数、setup 和样品版本。"
          />
        </section>
      )}
    </details>
  );
}

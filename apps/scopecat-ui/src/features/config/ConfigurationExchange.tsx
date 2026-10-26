import * as Controls from "../../ui/Controls";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { MethodResponse } from "openapi-fetch";
import { ApiError, apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import { errorMessage } from "../../lib/presentation";
import { getDevices } from "../instruments/device-api";
import { getParameterRevisions } from "./parameter-api";
import { getSetupDefinitions } from "./setup-api";

type Document = Inspection["document"];
type Inspection = MethodResponse<
  typeof apiClient,
  "get",
  "/api/v1/configuration-exchange/imports/{content_hash}"
>;
type Receipt = MethodResponse<typeof apiClient, "post", "/api/v1/configuration-exchange/derive">;
type Derive = components["schemas"]["ConfigurationDerive"];
const root = "/api/v1/configuration-exchange";
const maxBytes = 16 * 1024 * 1024;

async function checked(response: Response) {
  if (!response.ok) {
    const detail = (await response.json()) as { detail?: unknown };
    throw new Error(
      typeof detail.detail === "string" ? detail.detail : `Request failed (${response.status})`,
    );
  }
  return response;
}
async function inspect(document: string, retain = false): Promise<Inspection> {
  return (
    await checked(
      await fetch(`${root}/${retain ? "imports" : "inspect"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: document,
      }),
    )
  ).json() as Promise<Inspection>;
}
function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ConfigurationExchange({
  operator,
  resolveOperator,
  operatorOverride,
  onCreated,
}: {
  operator: string;
  resolveOperator?: () => Promise<string>;
  operatorOverride?: string;
  onCreated: (revision: string) => Promise<void>;
}) {
  const cache = useQueryClient();
  const versions = useQuery({
    queryKey: ["parameter-revisions"],
    queryFn: ({ signal }) => getParameterRevisions(signal),
  });
  const setups = useQuery({
    queryKey: ["setup-definitions"],
    queryFn: ({ signal }) => getSetupDefinitions(signal),
  });
  const devices = useQuery({ queryKey: ["devices"], queryFn: ({ signal }) => getDevices(signal) });
  const sources = useQuery({
    queryKey: ["exchange-sources"],
    queryFn: () => apiData(apiClient.GET("/api/v1/author-workspaces")),
  });
  const imports = useQuery({
    queryKey: ["configuration-imports"],
    queryFn: () => apiData(apiClient.GET("/api/v1/configuration-exchange/imports")),
  });
  const [exporting, setExporting] = useState(false);
  const [version, setVersion] = useState("");
  const [setup, setSetup] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [label, setLabel] = useState("");
  const [values, setValues] = useState(true);
  const [exported, setExported] = useState<Document>();
  const [review, setReview] = useState<Inspection>();
  const [name, setName] = useState("");
  const [includeSetup, setIncludeSetup] = useState(false);
  const [bindings, setBindings] = useState<Record<string, string>>({});
  const [receipt, setReceipt] = useState<Receipt>();
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const loading = useRef(0);
  const operation = useRef<{ intent: string; id: string; actor: string } | undefined>(undefined);
  const sourceState = useQuery({
    queryKey: ["exchange-source-state", workspace],
    enabled: !!workspace,
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/author-revisions", {
          params: { header: { "X-Scopecat-Workspace": workspace } },
        }),
      ),
  });
  const clearReview = () => {
    loading.current++;
    setReview(undefined);
    setReceipt(undefined);
    setError("");
    setNotice("");
    operation.current = undefined;
  };
  const openReview = (next: Inspection) => {
    setReview(next);
    setName(`${next.document.label} (copy)`);
    setIncludeSetup(false);
    setBindings({});
    setReceipt(undefined);
    setError("");
    setNotice("");
    operation.current = undefined;
  };
  const readFile = async (file: File) => {
    clearReview();
    saveCopy.reset();
    const request = loading.current;
    setBusy(true);
    try {
      if (file.size > maxBytes) throw new Error("配置文件超过 16 MiB。");
      const next = await inspect(await file.text());
      if (request === loading.current) openReview(next);
    } catch (problem) {
      if (request === loading.current) setError(errorMessage(problem));
    } finally {
      if (request === loading.current) setBusy(false);
    }
  };
  const exportingMutation = useMutation({
    mutationFn: () =>
      apiData(
        apiClient.POST("/api/v1/configuration-exchange/export", {
          body: {
            parameter_revision: version,
            label: label.trim(),
            include_values: values,
            setup_definition: setup || null,
            workspace: workspace || null,
            source_revision: workspace ? sourceState.data?.active?.content_hash : null,
          },
        }),
      ),
    onSuccess: setExported,
  });
  const saveCopy = useMutation({
    mutationFn: async () => {
      if (!review) throw new Error("请先检查配置。");
      const mapped: NonNullable<Derive["bindings"]> = {};
      if (includeSetup)
        for (const role of review.document.setup?.definition.instruments ?? []) {
          const device = devices.data?.items.find((item) => item.device.id === bindings[role.id]);
          if (!device || device.device.state === "retired")
            throw new Error(`Choose a local device for ${role.id}.`);
          mapped[role.id] = device.device.head;
        }
      const fields = {
        document: review.document,
        name: name.trim(),
        actor: operator,
        include_setup: includeSetup,
        bindings: mapped,
      };
      const intent = JSON.stringify({
        ...fields,
        actor: resolveOperator ? (operatorOverride ?? null) : operator,
      });
      if (operation.current?.intent !== intent) {
        fields.actor = resolveOperator ? await resolveOperator() : operator;
        operation.current = {
          intent,
          id: crypto.randomUUID(),
          actor: fields.actor,
        };
      }
      return apiData(
        apiClient.POST("/api/v1/configuration-exchange/derive", {
          body: { ...fields, actor: operation.current.actor, operation_id: operation.current.id },
        }),
      );
    },
    onSuccess: async (created) => {
      setReceipt(created);
      setNotice("");
      await cache.invalidateQueries({ queryKey: ["configuration-imports"] });
      await cache.invalidateQueries({ queryKey: ["setup-definitions"] });
      await onCreated(created.branch.revision.revision_id);
    },
  });
  const keep = async () => {
    if (!review) return;
    setBusy(true);
    setError("");
    try {
      await inspect(JSON.stringify(review.document), true);
      await cache.invalidateQueries({ queryKey: ["configuration-imports"] });
      setNotice("原件已保存以供核对，未启用任何参数、setup 或源码。");
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };
  const saveExport = async () => {
    if (!exported) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const content = JSON.stringify(exported);
      if (window.pywebview) {
        const path = await window.pywebview.api.save_configuration(content);
        setNotice(path ? `配置已保存到 ${path}。` : "已取消保存配置。");
      } else {
        download(new Blob([content], { type: "application/json" }), "configuration.json");
        setNotice("已请求下载配置。请在浏览器下载中查找 configuration.json。");
      }
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };
  const acceptSource = async () => {
    if (!review) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await inspect(JSON.stringify(review.document), true);
      if (window.pywebview) {
        const path = await window.pywebview.api.save_configuration_source(review.content_hash);
        setNotice(
          path
            ? `源码已保存到 ${path}。请解压到新目录后在应用设置中继续。尚未登记，也不能运行。`
            : "已取消保存源码。原件仍可用，未登记源码。",
        );
      } else {
        const response = await checked(
          await fetch(
            `${root}/imports/${encodeURIComponent(review.content_hash)}/source?accepted=true`,
            { method: "POST" },
          ),
        );
        download(await response.blob(), "author-source.zip");
        setNotice(
          "已请求下载源码。请检查浏览器下载，解压到新目录后在应用设置中继续。源码尚未登记，也不能运行。",
        );
      }
      await cache.invalidateQueries({ queryKey: ["configuration-imports"] });
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      className="grid gap-3 rounded-lg border border-line bg-panel p-4"
      aria-label="分享与导入配置"
    >
      <h3 className="text-base font-semibold leading-6">分享与导入配置</h3>
      <p>
        分享已保存的参数定义，以及可选的初始值、setup
        和保留源码。无需已有采集记录。副本可在下方常规参数与 setup 编辑器中编辑。
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <Controls.Button
          onClick={() => {
            setExporting(true);
            setExported(undefined);
          }}
        >
          导出配置…
        </Controls.Button>
        <Controls.Field>
          导入配置文件{" "}
          <Controls.Input
            type="file"
            accept=".json,application/json"
            disabled={busy || saveCopy.isPending}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void readFile(file);
              event.target.value = "";
            }}
          />
        </Controls.Field>
      </div>
      {exporting && (
        <fieldset
          disabled={exportingMutation.isPending}
          className="grid gap-3 border border-line p-3"
        >
          <legend>选择要分享的已保存内容</legend>
          <p>不包含未保存修改。请先在常规编辑器中保存，或选择已有的保存版本。</p>
          <Controls.Field>
            参数版本{" "}
            <Controls.Select
              value={version}
              onChange={(event) => {
                setVersion(event.target.value);
                setExported(undefined);
              }}
            >
              <option value="">选择版本</option>
              {versions.data?.items.map((item) => (
                <option key={item.id}>{item.id}</option>
              ))}
            </Controls.Select>
          </Controls.Field>
          <Controls.Field>
            文件标签{" "}
            <Controls.Input
              value={label}
              onChange={(event) => {
                setLabel(event.target.value);
                setExported(undefined);
              }}
            />
          </Controls.Field>
          <Controls.Field>
            <input
              type="checkbox"
              checked={values}
              onChange={(event) => {
                setValues(event.target.checked);
                setExported(undefined);
              }}
            />
            包含当前数值作为初始输入（不转移校准资格）
          </Controls.Field>
          <Controls.Field>
            setup（可选）{" "}
            <Controls.Select
              value={setup}
              onChange={(event) => {
                setSetup(event.target.value);
                setExported(undefined);
              }}
            >
              <option value="">仅参数</option>
              {setups.data?.items.map((item) => (
                <option key={item.id}>{item.id}</option>
              ))}
            </Controls.Select>
          </Controls.Field>
          <Controls.Field>
            保留源码（可选）{" "}
            <Controls.Select
              value={workspace}
              onChange={(event) => {
                setWorkspace(event.target.value);
                setExported(undefined);
              }}
            >
              <option value="">使用接收方已有源码</option>
              {sources.data?.items.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </Controls.Select>
          </Controls.Field>
          {workspace && (
            <p>
              {sourceState.data?.active
                ? "包含保留的源码版本，不包含未保存的文件修改。"
                : "没有可用的保留版本。请先在实验页准备可信作者代码，无需采集数据。"}
            </p>
          )}
          <p>
            设备连接地址和 SDK 安装不会作为设备绑定导出。请检查源码文件和自定义 setup
            中是否包含机器信息。
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <Controls.Button
              disabled={exportingMutation.isPending}
              onClick={() => setExporting(false)}
            >
              取消导出
            </Controls.Button>
            <Controls.Button
              disabled={
                !version ||
                !label.trim() ||
                exportingMutation.isPending ||
                (!!workspace && !sourceState.data?.active)
              }
              onClick={() => exportingMutation.mutate()}
            >
              核对导出内容
            </Controls.Button>
          </div>
          {exportingMutation.error && <p role="alert">{errorMessage(exportingMutation.error)}</p>}
          {exported && (
            <>
              <Content document={exported} />
              <Controls.Button disabled={busy} onClick={() => void saveExport()}>
                保存配置文件
              </Controls.Button>
            </>
          )}
        </fieldset>
      )}
      {imports.data && imports.data.length > 0 && (
        <details>
          <summary>已导入的原件（最近 100 条）</summary>
          {imports.data.map((item) => (
            <div key={item.content_hash}>
              <Controls.Button
                disabled={busy || saveCopy.isPending}
                onClick={async () => {
                  setBusy(true);
                  try {
                    openReview(
                      await apiData(
                        apiClient.GET("/api/v1/configuration-exchange/imports/{content_hash}", {
                          params: { path: { content_hash: item.content_hash } },
                        }),
                      ),
                    );
                    saveCopy.reset();
                  } catch (problem) {
                    setError(errorMessage(problem));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {item.label}
              </Controls.Button>
              <span>
                {" "}
                {item.derivations.map((entry) => entry.branch.name).join(", ") ||
                  "已保留以供核对；未创建副本"}
              </span>
            </div>
          ))}
        </details>
      )}
      {review && (
        <fieldset className="grid gap-3 border border-line p-3">
          <legend>检查收到的配置</legend>
          <Content document={review.document} />
          {review.notices.map((message) => (
            <p key={message}>{message}</p>
          ))}
          <details>
            <summary>原件标识与内容</summary>
            <p>
              {review.document.origin_store} · {review.content_hash}
            </p>
            <pre className="max-h-80 overflow-auto">{JSON.stringify(review.document, null, 2)}</pre>
          </details>
          {!receipt && (
            <>
              <Controls.Field>
                新参数分支名称{" "}
                <Controls.Input
                  value={name}
                  disabled={saveCopy.isPending}
                  onChange={(event) => {
                    setName(event.target.value);
                    saveCopy.reset();
                  }}
                />
              </Controls.Field>
              <p>创建新分支和版本。已有名称会被拒绝，不会覆盖任何内容，原件保持不变。</p>
              {review.document.setup && (
                <>
                  <Controls.Field>
                    <input
                      type="checkbox"
                      checked={includeSetup}
                      disabled={saveCopy.isPending}
                      onChange={(event) => {
                        setIncludeSetup(event.target.checked);
                        saveCopy.reset();
                      }}
                    />
                    同时创建我的 setup
                  </Controls.Field>
                  {!includeSetup && (
                    <p>
                      仅创建参数。原 setup
                      保留供后续核对。以后添加时需另建命名副本，并明确指定本地绑定。
                    </p>
                  )}
                  {includeSetup &&
                    review.document.setup.definition.instruments.map((role) => (
                      <Controls.Field key={role.id}>
                        本地设备对应角色 {role.id}
                        <Controls.Select
                          value={bindings[role.id] ?? ""}
                          disabled={saveCopy.isPending}
                          onChange={(event) => {
                            setBindings({ ...bindings, [role.id]: event.target.value });
                            saveCopy.reset();
                          }}
                        >
                          <option value="">请明确选择</option>
                          {devices.data?.items
                            .filter((item) => item.device.state !== "retired")
                            .map((item) => (
                              <option key={item.device.id} value={item.device.id}>
                                {item.device.label}
                              </option>
                            ))}
                        </Controls.Select>
                      </Controls.Field>
                    ))}
                  {includeSetup && (
                    <p>
                      缺少设备？可先保留原件，再前往登记： <a href="#instruments">设备与驱动</a>.
                      <Controls.Button onClick={() => void devices.refetch()}>
                        刷新本地设备
                      </Controls.Button>
                      导入不会连接设备。
                    </p>
                  )}
                </>
              )}
              <div className="flex flex-wrap items-end gap-3">
                <Controls.Button
                  disabled={busy || saveCopy.isPending}
                  onClick={() => {
                    clearReview();
                    saveCopy.reset();
                  }}
                >
                  取消检查
                </Controls.Button>
                <Controls.Button disabled={busy || saveCopy.isPending} onClick={() => void keep()}>
                  保留原件供以后使用
                </Controls.Button>
                <Controls.Button
                  disabled={
                    !name.trim() ||
                    !operator.trim() ||
                    busy ||
                    saveCopy.isPending ||
                    (includeSetup &&
                      (review.document.setup?.definition.instruments ?? []).some(
                        (role) => !bindings[role.id],
                      ))
                  }
                  onClick={() => saveCopy.mutate()}
                >
                  创建我的副本
                </Controls.Button>
              </div>
            </>
          )}
          {saveCopy.error && (
            <p role="alert">
              {errorMessage(saveCopy.error)}{" "}
              {saveCopy.error instanceof ApiError && saveCopy.error.status
                ? "请求已被拒绝。重试前请核对名称和本地绑定。"
                : "结果尚未确认。请重试未修改的请求，或重新打开原件检查已保存副本。"}
            </p>
          )}
          {receipt && (
            <div role="status">
              <p>
                已创建参数分支“{receipt.branch.name}
                ”。其保存版本已在下方参数版本中选中。请先完成已有未保存草稿，再选择“编辑副本”修改并保存导入版本。
              </p>
              {receipt.setup && (
                <p>
                  已创建 setup“{receipt.setup.resolution.definition_id}”。在下方实验 setup
                  中选中即可编辑映射。
                </p>
              )}
              {receipt.setup_pending && (
                <p>
                  未导入 setup。以后导入时请重新打开此原件，选择本地设备，另建包含 setup
                  的命名副本。
                </p>
              )}
              <p>未转移校准资格。运行前请使用常规实验流程重新预览。</p>
              <Controls.Button
                onClick={() => {
                  clearReview();
                  saveCopy.reset();
                }}
              >
                完成
              </Controls.Button>
            </div>
          )}
          {review.document.source && (
            <section aria-label="源码后续步骤">
              <h4>源码尚未准备好运行</h4>
              <p>
                请核对上方文件和依赖。接受仅下载不执行的归档，不会登记代码、准备依赖、加载实验或创建可运行计划。
              </p>
              <Controls.Button
                disabled={busy || saveCopy.isPending}
                onClick={() => void acceptSource()}
              >
                接受源码并下载…
              </Controls.Button>
              <ol>
                <li>将下载的归档解压到新目录。</li>
                <li>
                  打开 <a href="#settings">应用设置</a>，添加该目录并选择执行 Python，或明确准备其
                  pyproject.toml 环境。
                </li>
                <li>
                  在实验页选择该源码及本地参数/setup。保存计划或运行前，核对可信加载并重新预览。
                </li>
              </ol>
              <p>完成这些步骤前，此次导入仅提供可编辑输入，尚不是可复用的运行方法。</p>
            </section>
          )}
        </fieldset>
      )}
      {busy && <p role="status">正在检查或保存…</p>}
      {notice && <p role="status">{notice}</p>}
      {(error ||
        imports.error ||
        devices.error ||
        versions.error ||
        setups.error ||
        sources.error ||
        sourceState.error) && (
        <p role="alert">
          {error ||
            errorMessage(
              imports.error ??
                devices.error ??
                versions.error ??
                setups.error ??
                sources.error ??
                sourceState.error,
            )}
        </p>
      )}
    </section>
  );
}

function Content({ document }: { document: Document }) {
  return (
    <section aria-label="选中的配置内容">
      <h4>{document.label}</h4>
      <p>
        {document.catalog.definitions?.length ?? 0} 项参数定义 ·{" "}
        {document.values_included ? "包含初始值" : "仅定义；数值未知"}
      </p>
      <ul>
        {(document.catalog.definitions ?? []).map((definition) => (
          <li key={definition.id}>
            <strong>{definition.id}</strong>
            {definition.description ? ` — ${definition.description}` : ""}
          </li>
        ))}
      </ul>
      <details>
        <summary>参数 schema 与数值</summary>
        <pre className="max-h-64 overflow-auto">
          {JSON.stringify({ catalog: document.catalog, parameters: document.parameters }, null, 2)}
        </pre>
      </details>
      {document.setup && (
        <details>
          <summary>Setup {document.setup.id} — 需要本地核对</summary>
          <pre className="max-h-64 overflow-auto">
            {JSON.stringify(document.setup.definition, null, 2)}
          </pre>
        </details>
      )}
      {document.source && (
        <details>
          <summary>附带源码文件与依赖</summary>
          <ul>
            {Object.entries(document.source.files).map(([name, encoded]) => (
              <li key={name}>
                <details>
                  <summary>{name}</summary>
                  <SourceText encoded={encoded} />
                </details>
              </li>
            ))}
          </ul>
          <p>
            Python {document.source.manifest.python}；依赖：{" "}
            {(document.source.manifest.import_requirements ?? []).join(", ") || "未记录明确依赖"}
          </p>
          <p>保留文件内容但不执行，不承诺静态重建任意方法。</p>
        </details>
      )}
    </section>
  );
}

function SourceText({ encoded }: { encoded: string }) {
  if (encoded.length > 512 * 1024) return <p>文件较大；可信加载前请检查下载的归档。</p>;
  let content: string;
  try {
    const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
    content = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    if (content.includes("\0")) throw new Error("Binary content");
  } catch {
    return <p>二进制文件；可信加载前请检查下载的归档。</p>;
  }
  return <pre className="max-h-64 overflow-auto whitespace-pre-wrap">{content}</pre>;
}

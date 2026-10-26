import type { ConfigRegistryEntry } from "../../api-contract";

export type ConfigProvenanceSource =
  | ConfigRegistryEntry["source"]
  | NonNullable<
      Extract<ConfigRegistryEntry["source"], { kind: "parameter_context" }>["publication"]
    >;

export function filterConfigEntries(
  entries: ConfigRegistryEntry[],
  search: string,
): ConfigRegistryEntry[] {
  const query = search.trim().toLocaleLowerCase();
  if (!query) return entries;
  return entries.filter((entry) => {
    const sourceTerms = configSourceSearchTerms(entry.source);
    return [entry.id, entry.actor, entry.note, entry.source.kind, ...sourceTerms]
      .filter((value): value is string => value !== undefined)
      .join(" ")
      .toLocaleLowerCase()
      .includes(query);
  });
}

export function configSourceLabel(entry: ConfigRegistryEntry): string {
  switch (entry.source.kind) {
    case "direct_config_profile":
      return "直接 profile";
    case "parameter_revision":
      return "参数版本";
    case "bound_parameters":
      return "已准备参数输入";
    case "manual_parameter_updates":
      return "类型化参数编辑";
    case "setup_rebind":
      return "明确的 setup 重新绑定";
    case "parameter_context":
      if (entry.source.rebind) return "工作点 setup 重新绑定";
      return entry.source.publication ? "已验证工作点" : "参数上下文";
    case "candidate_config":
      return "候选配置";
    default:
      return assertNever(entry.source);
  }
}

function configSourceSearchTerms(source: ConfigProvenanceSource): Array<string | null | undefined> {
  switch (source.kind) {
    case "bound_parameters":
      return [source.setup.revision_id, source.parameters.revision_id];
    case "parameter_revision":
      return [source.setup.revision_id];
    case "direct_config_profile":
    case "manual_parameter_updates":
      return [];
    case "setup_rebind":
      return [source.base.entry_id, source.setup.revision_id];
    case "parameter_context":
      return [
        ...(source.rebind ? configSourceSearchTerms(source.rebind) : []),
        source.context.workspace_id,
        ...(source.publication ? configSourceSearchTerms(source.publication) : []),
        source.context.label,
        source.context.working_point_id,
        source.context.sample.sample_id,
        source.context.base.entry_id,
      ];
    case "candidate_config":
      return [source.run_id, source.proposal_id];
    default:
      return assertNever(source);
  }
}

function assertNever(value: never): never {
  throw new Error(`Unsupported config source: ${JSON.stringify(value)}`);
}

export function safeConfigEntryId(value: string): string {
  const normalized = value
    .trim()
    .replace(/[^A-Za-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return /^[A-Za-z0-9]/.test(normalized) ? normalized : `config-${normalized}`;
}

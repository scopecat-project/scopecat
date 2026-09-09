import type { ConfigRegistryEntry, ConfigRegistryOverview } from "../../api-contract";

export interface ConfigUndoTarget {
  entryId: string;
  expectedGeneration: number;
}

export function configUndoTarget(overview: ConfigRegistryOverview): ConfigUndoTarget | undefined {
  const active = overview.activation;
  if (active == null) return undefined;
  const previous = overview.activation_history.reduce<
    ConfigRegistryOverview["activation_history"][number] | undefined
  >((selected, record) => {
    if (record.generation >= active.generation || record.entry_id === active.entry_id) {
      return selected;
    }
    return selected === undefined || record.generation > selected.generation ? record : selected;
  }, undefined);
  return previous === undefined
    ? undefined
    : { entryId: previous.entry_id, expectedGeneration: active.generation };
}

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
      return "Direct profile";
    case "manual_parameter_updates":
      return "Typed parameter edit";
    case "parameter_context":
      return "Parameter context";
    case "candidate_config":
      return "Candidate config";
    case "calibration_cohort_merge":
      return "Calibration cohort merge";
    default:
      return assertNever(entry.source);
  }
}

function configSourceSearchTerms(
  source: ConfigRegistryEntry["source"],
): Array<string | null | undefined> {
  switch (source.kind) {
    case "direct_config_profile":
    case "manual_parameter_updates":
      return [];
    case "parameter_context":
      return [
        source.context.label,
        source.context.working_point_id,
        source.context.sample.sample_id,
        source.context.base.entry_id,
      ];
    case "candidate_config":
      return [source.run_id, source.proposal_id];
    case "calibration_cohort_merge":
      return [
        source.cohort_id,
        source.spec_hash,
        source.candidate_id,
        source.base_entry_id,
        source.base_config_content_hash,
        source.composition_policy_ref.id,
        source.composition_policy_ref.version,
        source.composition_policy_ref.fingerprint,
        source.automatic_publication_policy_id,
        source.automatic_publication_policy_version,
        source.automatic_publication_policy_fingerprint,
        ...source.contributions.flatMap((contribution) => [
          contribution.member_id,
          contribution.proof.kind,
          contribution.proof.evidence_step.procedure_run_id,
          contribution.proof.evidence_step.step_key,
          contribution.proof.baseline_run_id,
          contribution.proof.candidate_run_id,
          contribution.proof.proposal_id,
          contribution.proof.fit_analysis_record_id,
          contribution.proof.decision.analysis_record_id,
          contribution.proof.decision.output_id,
          contribution.result_input_fingerprint,
        ]),
      ];
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

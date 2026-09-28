import { useEffect, useMemo, useState } from "react";
import type {
  InstrumentDescription,
  InstrumentInterface,
  InstrumentProperty,
  InstrumentSpec,
  InstrumentStateSetting,
  InstrumentStateTarget,
} from "../../api-contract";
import { titleCase } from "../../lib/presentation";
import { InstrumentPropertyInput, type InstrumentPropertyDraft } from "./InstrumentPropertyInput";
import {
  deviceStateTarget,
  interfaceMountPaths,
  interfaceStateTarget,
  resolveInterfaceProperty,
  stateMemberKey,
} from "./InstrumentInterfaceControls";

type RunStartPolicy = InstrumentSpec["run_start"];
type InterfaceMember = Pick<InstrumentInterface, "properties" | "components">;

interface DefaultMember {
  target: InstrumentStateTarget;
  property: InstrumentProperty;
  path: string[];
}

interface DefaultGroup {
  id: string;
  label: string;
  detail?: string;
  members: DefaultMember[];
}

export function InstrumentDefaultsEditor({
  mode = "startup",
  description,
  defaultState,
  runStart,
  onDefaultStateChange,
  onRunStartChange,
  onValidityChange,
}: {
  mode?: "startup" | "safety";
  description?: InstrumentDescription;
  defaultState: InstrumentStateSetting[];
  runStart: RunStartPolicy;
  onDefaultStateChange: (defaultState: InstrumentStateSetting[]) => void;
  onRunStartChange: (runStart: RunStartPolicy) => void;
  onValidityChange: (valid: boolean) => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, InstrumentPropertyDraft>>({});
  const invalid = Object.values(drafts).some((draft) => draft.value === undefined);
  const groups = useMemo(() => defaultGroups(description), [description]);

  useEffect(() => onValidityChange(!invalid), [invalid, onValidityChange]);

  const toggle = (member: DefaultMember, enabled: boolean) => {
    const key = stateMemberKey(member.target);
    if (!enabled) {
      setDrafts((current) => withoutDraft(current, key));
      onDefaultStateChange(defaultState.filter((item) => stateMemberKey(item.target) !== key));
      return;
    }
    const initial = initialDraft(member.property);
    setDrafts((current) => ({ ...current, [key]: initial }));
    if (initial.value !== undefined) {
      onDefaultStateChange(
        replaceDefault(defaultState, { target: member.target, value: initial.value }),
      );
    }
  };

  const edit = (member: DefaultMember, draft: InstrumentPropertyDraft) => {
    const key = stateMemberKey(member.target);
    setDrafts((current) => ({ ...current, [key]: draft }));
    if (draft.value === undefined) return;
    onDefaultStateChange(
      replaceDefault(defaultState, { target: member.target, value: draft.value }),
    );
  };

  return (
    <section className="grid gap-2.5 border-y border-line py-3">
      <header className="flex items-end justify-between gap-3.5 max-[460px]:flex-col max-[460px]:items-stretch">
        <div className="grid gap-[3px]">
          <strong className="text-[0.66rem] text-text-soft">
            {mode === "safety" ? "Safe state" : "Experiment start"}
          </strong>
          <small className="text-[0.56rem] leading-normal text-text-dim">
            {mode === "safety"
              ? "These device constraints apply to every experiment that uses this device."
              : "Synchronize first, then optionally apply this sparse member state."}
          </small>
        </div>
        {mode === "startup" && (
          <label className="grid min-w-[210px] gap-[5px] max-[460px]:min-w-0">
            <span className="text-[0.53rem] font-extrabold tracking-[0.07em] text-text-dim uppercase">
              Start policy
            </span>
            <select
              className="min-h-[34px] w-full min-w-0 rounded-sm border border-line bg-bg px-[9px] text-[0.64rem] text-text outline-0 focus:border-accent"
              value={runStart}
              onChange={(event) => onRunStartChange(event.target.value as RunStartPolicy)}
            >
              <option value="preserve">Preserve observed state</option>
              <option value="apply_default_state">Apply configured defaults</option>
            </select>
          </label>
        )}
      </header>

      {description ? (
        <div className="grid max-h-[330px] gap-2.5 overflow-auto pr-[3px] [scrollbar-color:#344252_transparent] [scrollbar-width:thin]">
          {groups.map((group) => (
            <section className="grid gap-[7px]" key={group.id}>
              <h5 className="m-0 text-[0.54rem] font-extrabold tracking-[0.05em] text-text-dim uppercase">
                {group.label}
              </h5>
              {group.detail && <code className="text-[0.51rem] text-text-dim">{group.detail}</code>}
              <div className="grid gap-1.5">
                {group.members.map((member) => {
                  const key = stateMemberKey(member.target);
                  const assignment = defaultState.find(
                    (item) => stateMemberKey(item.target) === key,
                  );
                  const enabled = assignment !== undefined || key in drafts;
                  return (
                    <div
                      className="grid grid-cols-[minmax(180px,1fr)_minmax(170px,0.9fr)] items-center gap-2.5 rounded-sm border border-line bg-panel-soft px-2 py-[7px] max-[680px]:grid-cols-2 max-[460px]:grid-cols-1"
                      data-testid={`instrument-default-property-${member.property.id}`}
                      key={key}
                    >
                      <label className="flex min-w-0 items-center gap-2">
                        <input
                          className="min-h-[15px]! w-[15px] flex-none p-0! accent-accent"
                          type="checkbox"
                          checked={enabled}
                          aria-label={`Configure default for ${memberLabel(member)}`}
                          onChange={(event) => toggle(member, event.target.checked)}
                        />
                        <span className="grid min-w-0 gap-0.5">
                          <strong className="overflow-hidden text-[0.61rem] text-ellipsis whitespace-nowrap text-text-soft">
                            {memberLabel(member)}
                          </strong>
                        </span>
                      </label>
                      <InstrumentPropertyInput
                        property={member.property}
                        currentValue={assignment?.value}
                        draft={drafts[key]}
                        editable={enabled}
                        ariaLabel={`${memberLabel(member)} default value`}
                        onChange={(draft) => edit(member, draft)}
                      />
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
          {groups.length === 0 && (
            <p className={defaultsUnavailable}>This driver declares no writable state members.</p>
          )}
        </div>
      ) : (
        <p className={defaultsUnavailable}>
          Test the connection to load driver-declared default settings.
          {defaultState.length > 0 &&
            ` ${defaultState.length} existing ${
              defaultState.length === 1 ? "value is" : "values are"
            } preserved.`}
        </p>
      )}

      {runStart === "apply_default_state" && defaultState.length === 0 && (
        <p className={configNote} role="status">
          Select at least one default value before enabling automatic apply.
        </p>
      )}
    </section>
  );
}

const configNote =
  "m-0 rounded-sm border border-[rgb(128_163_207_/_20%)] bg-accent-soft px-2.5 py-[9px] text-[0.58rem] leading-normal text-text-dim";
const defaultsUnavailable =
  "m-0 rounded-sm border border-dashed border-line px-2.5 py-[9px] text-[0.56rem] leading-normal text-text-dim";

function defaultGroups(description: InstrumentDescription | undefined): DefaultGroup[] {
  if (!description) return [];
  const groups: DefaultGroup[] = [];
  for (const instrumentInterface of description.interfaces ?? []) {
    for (const mountPath of interfaceMountPaths(description, instrumentInterface.id)) {
      const members: DefaultMember[] = [];
      collectInterfaceMembers(
        members,
        description,
        instrumentInterface.id,
        mountPath,
        instrumentInterface,
      );
      if (members.length === 0) continue;
      groups.push({
        id: `interface:${instrumentInterface.id}:${mountPath.join("/")}`,
        label: instrumentInterface.label ?? interfaceLabel(instrumentInterface.id),
        detail: mountPath.length > 0 ? mountPath.join(" / ") : undefined,
        members,
      });
    }
  }
  for (const schema of description.device_schemas ?? []) {
    const members = (schema.members ?? []).flatMap((member): DefaultMember[] => {
      if (member.property.access === "read_only") return [];
      const path = member.component_path ?? [];
      return [
        {
          target: deviceStateTarget(schema.id, path, member.property.id),
          property: member.property,
          path,
        },
      ];
    });
    if (members.length > 0) {
      groups.push({
        id: `device:${schema.id}`,
        label: schema.label ?? interfaceLabel(schema.id),
        detail: schema.id,
        members,
      });
    }
  }
  return groups;
}

function collectInterfaceMembers(
  members: DefaultMember[],
  description: InstrumentDescription,
  interfaceId: string,
  componentPath: string[],
  member: InterfaceMember,
): void {
  for (const property of member.properties ?? []) {
    const target = interfaceStateTarget(interfaceId, componentPath, property.id);
    const effective = resolveInterfaceProperty(description, target, property);
    if (effective.access !== "read_only") {
      members.push({ target, property: effective, path: componentPath });
    }
  }
  for (const child of member.components ?? []) {
    collectInterfaceMembers(members, description, interfaceId, [...componentPath, child.id], child);
  }
}

function memberLabel(member: DefaultMember): string {
  const label = member.property.label ?? titleCase(member.property.id);
  return member.path.length > 0 ? `${member.path.map(titleCase).join(" / ")} · ${label}` : label;
}

function interfaceLabel(id: string): string {
  const qualifiedName = id.slice(0, id.lastIndexOf("/"));
  return titleCase(qualifiedName.slice(qualifiedName.lastIndexOf(".") + 1));
}

function initialDraft(property: InstrumentProperty): InstrumentPropertyDraft {
  if (property.value_type.type === "bool") return { raw: false, value: false };
  return { raw: "" };
}

function replaceDefault(
  defaultState: InstrumentStateSetting[],
  replacement: InstrumentStateSetting,
): InstrumentStateSetting[] {
  const key = stateMemberKey(replacement.target);
  const index = defaultState.findIndex((item) => stateMemberKey(item.target) === key);
  if (index < 0) return [...defaultState, replacement];
  return defaultState.map((item, itemIndex) => (itemIndex === index ? replacement : item));
}

function withoutDraft(
  current: Record<string, InstrumentPropertyDraft>,
  removed: string,
): Record<string, InstrumentPropertyDraft> {
  return Object.fromEntries(Object.entries(current).filter(([key]) => key !== removed));
}

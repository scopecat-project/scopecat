import * as Controls from "../../ui/Controls";
import { useState } from "react";
import { AlertTriangle, Database, LoaderCircle, Play } from "lucide-react";
import type {
  InstrumentCollectReceipt,
  InstrumentConfiguredDefaultsApplyReceipt,
  InstrumentDescription,
  InstrumentDeviceState,
  InstrumentInterface,
  InstrumentInvokeReceipt,
  InstrumentOperation,
  InstrumentProperty,
  InstrumentSession,
  InstrumentState,
  InstrumentStateObservation,
  InstrumentStateTarget,
  InstrumentView,
} from "../../api-contract";
import { errorMessage, titleCase } from "../../lib/presentation";
import { classes, secondaryButton } from "../../ui/styles";
import { InstrumentPropertyInput, type InstrumentPropertyDraft } from "./InstrumentPropertyInput";
import type {
  InstrumentAcquisitionTarget,
  InstrumentOperationArgument,
  InstrumentOperationTarget,
  StagedInstrumentMember,
} from "./instrument-api";

type DraftValue = InstrumentPropertyDraft;

export function InterfaceCard({
  description,
  instrumentInterface,
  mountPath,
  connected,
  state,
  drafts,
  collectResults,
  invokeResults,
  collectingTarget,
  invokingTarget,
  interactionDisabled,
  onStage,
  onCollect,
  onInvoke,
  onOperationEdit,
}: {
  description: InstrumentDescription;
  instrumentInterface: InstrumentInterface;
  mountPath: string[];
  connected: boolean;
  state?: InstrumentState;
  drafts: Record<string, DraftValue>;
  collectResults: Record<string, InstrumentCollectReceipt>;
  invokeResults: Record<string, InstrumentInvokeReceipt>;
  collectingTarget?: InstrumentAcquisitionTarget;
  invokingTarget?: InstrumentOperationTarget;
  interactionDisabled: boolean;
  onStage: (target: InstrumentStateTarget, draft?: DraftValue) => void;
  onCollect: (target: InstrumentAcquisitionTarget) => void;
  onInvoke: (
    target: InstrumentOperationTarget,
    operationArguments: InstrumentOperationArgument[],
  ) => void;
  onOperationEdit: (target: InstrumentOperationTarget) => void;
}) {
  return (
    <article
      className="overflow-hidden rounded-md border border-line bg-panel-soft"
      data-testid={`interface-card-${instrumentInterface.id}-${mountPath.join("-") || "root"}`}
    >
      <header className="flex items-start justify-between gap-3.5 border-b border-line bg-[rgb(255_255_255_/_1%)] px-[13px] py-3 max-[460px]:flex-col">
        <div>
          <h4 className="m-0 text-sm font-[680]">
            {instrumentInterface.label ?? interfaceFallbackLabel(instrumentInterface.id)}
          </h4>
          {instrumentInterface.description && (
            <p className="mt-1.5 mb-0 max-w-[760px] text-xs leading-[1.45] text-text-dim">
              {instrumentInterface.description}
            </p>
          )}
          {mountPath.length > 0 && (
            <code className="mt-1.5 block text-xs text-text-dim">{mountPath.join(" / ")}</code>
          )}
        </div>
      </header>

      <InterfaceEndpoint
        interfaceId={instrumentInterface.id}
        componentPath={mountPath}
        member={instrumentInterface}
        description={description}
        rootDepth={mountPath.length}
        connected={connected}
        state={state}
        drafts={drafts}
        collectResults={collectResults}
        invokeResults={invokeResults}
        collectingTarget={collectingTarget}
        invokingTarget={invokingTarget}
        interactionDisabled={interactionDisabled}
        onStage={onStage}
        onCollect={onCollect}
        onInvoke={onInvoke}
        onOperationEdit={onOperationEdit}
      />
    </article>
  );
}

export function DeviceStateCard({
  deviceState,
  connected,
  state,
  drafts,
  interactionDisabled,
  onStage,
}: {
  deviceState: InstrumentDeviceState;
  connected: boolean;
  state?: InstrumentState;
  drafts: Record<string, DraftValue>;
  interactionDisabled: boolean;
  onStage: (target: InstrumentStateTarget, draft?: DraftValue) => void;
}) {
  return (
    <article
      className="overflow-hidden rounded-md border border-line bg-panel-soft"
      data-testid={`device-state-card-${deviceState.id}`}
    >
      <header className="border-b border-line bg-[rgb(255_255_255_/_1%)] px-[13px] py-3">
        <h4 className="m-0 text-sm font-[680]">
          {deviceState.label ?? interfaceFallbackLabel(deviceState.id)}
        </h4>
        <code className="mt-1.5 block text-xs text-text-dim">{deviceState.id}</code>
        {deviceState.description && (
          <p className="mt-1.5 mb-0 max-w-[760px] text-xs leading-[1.45] text-text-dim">
            {deviceState.description}
          </p>
        )}
      </header>
      {(deviceState.members ?? []).length > 0 && (
        <div className={controlGrid}>
          {(deviceState.members ?? []).map((member) => {
            const target = deviceStateTarget(
              deviceState.id,
              member.component_path ?? [],
              member.property.id,
            );
            return (
              <PropertyEditor
                key={stateMemberKey(target)}
                property={member.property}
                observation={stateObservation(state, target)}
                draft={drafts[stateMemberKey(target)]}
                editable={
                  connected && !interactionDisabled && member.property.access !== "read_only"
                }
                onChange={(draft) => onStage(target, draft)}
              />
            );
          })}
        </div>
      )}
    </article>
  );
}

type InterfaceMember = Pick<
  InstrumentInterface,
  "label" | "description" | "properties" | "operations" | "acquisitions" | "components"
>;

function InterfaceEndpoint({
  description,
  interfaceId,
  componentPath,
  rootDepth,
  member,
  connected,
  state,
  drafts,
  collectResults,
  invokeResults,
  collectingTarget,
  invokingTarget,
  interactionDisabled,
  onStage,
  onCollect,
  onInvoke,
  onOperationEdit,
}: {
  description: InstrumentDescription;
  interfaceId: string;
  componentPath: string[];
  rootDepth: number;
  member: InterfaceMember;
  connected: boolean;
  state?: InstrumentState;
  drafts: Record<string, DraftValue>;
  collectResults: Record<string, InstrumentCollectReceipt>;
  invokeResults: Record<string, InstrumentInvokeReceipt>;
  collectingTarget?: InstrumentAcquisitionTarget;
  invokingTarget?: InstrumentOperationTarget;
  interactionDisabled: boolean;
  onStage: (target: InstrumentStateTarget, draft?: DraftValue) => void;
  onCollect: (target: InstrumentAcquisitionTarget) => void;
  onInvoke: (
    target: InstrumentOperationTarget,
    operationArguments: InstrumentOperationArgument[],
  ) => void;
  onOperationEdit: (target: InstrumentOperationTarget) => void;
}) {
  const properties = member.properties ?? [];
  const operations = member.operations ?? [];
  const hasLocalControls =
    properties.length > 0 || operations.length > 0 || (member.acquisitions ?? []).length > 0;
  return (
    <>
      {hasLocalControls && (
        <section className={componentPath.length > rootDepth ? "border-t border-line" : undefined}>
          {componentPath.length > rootDepth && (
            <header className="bg-panel-strong px-[13px] py-2.5">
              <h5 className="m-0 text-xs text-text-soft">
                {member.label ?? titleCase(componentPath.at(-1) ?? "")}
              </h5>
              <small className="mt-[3px] block text-xs text-text-dim">
                {componentPath.slice(rootDepth).map(titleCase).join(" / ")}
              </small>
              {member.description && <p>{member.description}</p>}
            </header>
          )}

          {properties.length > 0 && (
            <div className={controlGrid}>
              {properties.map((property) => {
                const target = interfaceStateTarget(interfaceId, componentPath, property.id);
                const effectiveProperty = resolveInterfaceProperty(description, target, property);
                const key = stateMemberKey(target);
                return (
                  <PropertyEditor
                    key={property.id}
                    property={effectiveProperty}
                    observation={stateObservation(state, target)}
                    draft={drafts[key]}
                    editable={
                      connected && !interactionDisabled && effectiveProperty.access !== "read_only"
                    }
                    onChange={(draft) => onStage(target, draft)}
                  />
                );
              })}
            </div>
          )}

          {operations.length > 0 && (
            <div className="grid">
              {operations.map((operation) => {
                const target = { interfaceId, componentPath, operation };
                const key = operationKey(target);
                if (requiresPayload(operation)) {
                  return <PreparedOperation key={operation.id} operation={operation} />;
                }
                return (
                  <OperationControl
                    key={operation.id}
                    target={target}
                    connected={connected}
                    result={invokeResults[key]}
                    invoking={invokingTarget !== undefined && operationKey(invokingTarget) === key}
                    interactionDisabled={interactionDisabled}
                    onInvoke={(operationArguments) => onInvoke(target, operationArguments)}
                    onEdit={() => onOperationEdit(target)}
                  />
                );
              })}
            </div>
          )}

          {(member.acquisitions ?? []).length > 0 && (
            <div className="grid">
              {(member.acquisitions ?? []).map((acquisition) => {
                const target = { interfaceId, componentPath, acquisition };
                const key = acquisitionKey(target);
                return (
                  <AcquisitionControl
                    key={acquisition.id}
                    target={target}
                    connected={connected}
                    result={collectResults[key]}
                    interactionDisabled={interactionDisabled}
                    collecting={
                      collectingTarget !== undefined && acquisitionKey(collectingTarget) === key
                    }
                    onCollect={() => onCollect(target)}
                  />
                );
              })}
            </div>
          )}
        </section>
      )}

      {!hasLocalControls && (member.components ?? []).length === 0 && (
        <p className="px-[13px] py-2 text-xs text-text-dim">
          {componentPath.length > rootDepth && <code>{componentPath.join(" / ")} — </code>}
          此接口未声明属性、operation 或 acquisition。请查看驱动文档或检查驱动版本。
        </p>
      )}
      {(member.components ?? []).map((child) => (
        <InterfaceEndpoint
          key={child.id}
          description={description}
          interfaceId={interfaceId}
          componentPath={[...componentPath, child.id]}
          rootDepth={rootDepth}
          member={child}
          connected={connected}
          state={state}
          drafts={drafts}
          collectResults={collectResults}
          invokeResults={invokeResults}
          collectingTarget={collectingTarget}
          invokingTarget={invokingTarget}
          interactionDisabled={interactionDisabled}
          onStage={onStage}
          onCollect={onCollect}
          onInvoke={onInvoke}
          onOperationEdit={onOperationEdit}
        />
      ))}
    </>
  );
}

function OperationControl({
  target,
  connected,
  result,
  invoking,
  interactionDisabled,
  onInvoke,
  onEdit,
}: {
  target: InstrumentOperationTarget;
  connected: boolean;
  result?: InstrumentInvokeReceipt;
  invoking: boolean;
  interactionDisabled: boolean;
  onInvoke: (operationArguments: InstrumentOperationArgument[]) => void;
  onEdit: () => void;
}) {
  const operation = target.operation;
  const argumentSpecs = operation.arguments ?? [];
  const [drafts, setDrafts] = useState<Record<string, DraftValue>>({});
  const operationArguments = argumentSpecs.flatMap((argument) => {
    const value = drafts[argument.id]?.value;
    return value === undefined ? [] : [{ id: argument.id, value }];
  });
  const invalid = operationArguments.length !== argumentSpecs.length;
  const label = operation.label ?? titleCase(operation.id);

  const edit = (argumentId: string, draft?: DraftValue) => {
    setDrafts((current) => {
      const next = { ...current };
      if (draft === undefined) delete next[argumentId];
      else next[argumentId] = draft;
      return next;
    });
    onEdit();
  };

  return (
    <section className="border-t border-line">
      <header className={controlHeader}>
        <div>
          <strong className="text-xs text-text-soft">{label}</strong>
          {operation.description && <p className={controlDescription}>{operation.description}</p>}
        </div>
        <button
          type="button"
          className={classes(secondaryButton, "flex-none")}
          aria-label={`调用 ${label}`}
          onClick={() => onInvoke(operationArguments)}
          disabled={!connected || interactionDisabled || invalid}
          title={
            !connected
              ? "调用前请连接"
              : interactionDisabled
                ? "请等待当前仪器交互完成"
                : invalid
                  ? "调用前请填写所有 operation 参数"
                  : undefined
          }
        >
          {invoking ? <LoaderCircle className="animate-spin" size={14} /> : <Play size={14} />}
          调用 Invoke
        </button>
      </header>
      {argumentSpecs.length > 0 && (
        <div className={controlGrid}>
          {argumentSpecs.map((argument) => (
            <OperationArgumentEditor
              key={argument.id}
              argument={argument}
              draft={drafts[argument.id]}
              editable={connected && !interactionDisabled}
              onChange={(draft) => edit(argument.id, draft)}
            />
          ))}
        </div>
      )}
      {result && (
        <p
          className={classes(
            receiptStyle,
            "rounded-none border-x-0 border-b-0",
            receiptTone(result.status),
          )}
        >
          Invoke 回执： {titleCase(result.status)}
        </p>
      )}
    </section>
  );
}

function OperationArgumentEditor({
  argument,
  draft,
  editable,
  onChange,
}: {
  argument: NonNullable<InstrumentOperation["arguments"]>[number];
  draft?: DraftValue;
  editable: boolean;
  onChange: (draft?: DraftValue) => void;
}) {
  const type = argument.value_type;
  if (type.type === "payload") return null;
  const label = argument.label ?? titleCase(argument.id);
  const raw = draft?.raw ?? "";
  return (
    <Controls.Field className={editorRow}>
      <span className={propertyLabel}>
        <span className="grid min-w-0">
          <strong className="overflow-hidden text-xs font-[670] text-ellipsis whitespace-nowrap">
            {label}
          </strong>
        </span>
        <small className="flex-none text-xs font-extrabold tracking-[0.04em] text-text-dim uppercase">
          参数
        </small>
      </span>
      {type.type === "bool" ? (
        <Controls.Select
          className={controlInput}
          value={typeof raw === "boolean" ? String(raw) : ""}
          disabled={!editable}
          onChange={(event) => {
            const value = event.target.value === "true";
            onChange({ raw: value, value });
          }}
        >
          <option value="" disabled>
            请选择…
          </option>
          <option value="true">开启</option>
          <option value="false">关闭</option>
        </Controls.Select>
      ) : type.type === "string" && type.choices ? (
        <Controls.Select
          className={controlInput}
          value={typeof raw === "string" ? raw : ""}
          disabled={!editable}
          onChange={(event) => onChange({ raw: event.target.value, value: event.target.value })}
        >
          <option value="" disabled>
            请选择…
          </option>
          {type.choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </Controls.Select>
      ) : type.type === "int" || type.type === "float" || type.type === "quantity" ? (
        <span
          className={classes(
            "grid items-stretch",
            type.type === "quantity"
              ? "grid-cols-[minmax(0,1fr)_auto]"
              : "grid-cols-[minmax(0,1fr)]",
          )}
        >
          <Controls.Input
            className={classes(controlInput, type.type === "quantity" && "rounded-r-none")}
            type="number"
            step={type.type === "int" ? 1 : "any"}
            min={type.minimum ?? undefined}
            max={type.maximum ?? undefined}
            value={typeof raw === "string" ? raw : ""}
            placeholder={editable ? "输入数值" : "—"}
            disabled={!editable}
            aria-invalid={draft !== undefined && draft.value === undefined}
            onChange={(event) =>
              onChange(operationArgumentDraft(type, event.target.value, draft?.unit))
            }
          />
          {type.type === "quantity" &&
            (type.unit ? (
              <span className={unitAddon}>{type.unit}</span>
            ) : (
              <Controls.Input
                className={classes(controlInput, "min-w-16 rounded-l-none border-l-0")}
                type="text"
                aria-label={`${label} 单位`}
                value={draft?.unit ?? ""}
                placeholder="单位"
                disabled={!editable}
                onChange={(event) =>
                  onChange(
                    operationArgumentDraft(
                      type,
                      typeof raw === "string" ? raw : "",
                      event.target.value,
                    ),
                  )
                }
              />
            ))}
        </span>
      ) : type.type === "string" ? (
        <Controls.Input
          className={controlInput}
          type="text"
          value={typeof raw === "string" ? raw : ""}
          placeholder={editable ? "输入数值" : "—"}
          disabled={!editable}
          onChange={(event) => onChange({ raw: event.target.value, value: event.target.value })}
        />
      ) : null}
      {argument.description && (
        <small className={propertyDescription}>{argument.description}</small>
      )}
      {draft && (
        <button type="button" className={propertyReset} onClick={() => onChange()}>
          清除参数
        </button>
      )}
    </Controls.Field>
  );
}

function AcquisitionControl({
  target,
  connected,
  result,
  collecting,
  interactionDisabled,
  onCollect,
}: {
  target: InstrumentAcquisitionTarget;
  connected: boolean;
  result?: InstrumentCollectReceipt;
  collecting: boolean;
  interactionDisabled: boolean;
  onCollect: () => void;
}) {
  const acquisition = target.acquisition;
  const results = acquisition.results;
  return (
    <section className="border-t border-line">
      <header className={controlHeader}>
        <div>
          <strong className="text-xs text-text-soft">
            {acquisition.label ?? titleCase(acquisition.id)}
          </strong>
          <p className={controlDescription}>
            {acquisition.description
              ? `采集契约：${acquisition.description}`
              : "此 acquisition 未说明是否发起新采集或收集既有结果；调用前请查阅驱动文档。"}
          </p>
        </div>
        {results.length > 0 && (
          <div className="grid max-w-80 flex-none justify-items-end gap-1.5">
            <button
              type="button"
              className={secondaryButton}
              onClick={onCollect}
              disabled={!connected || interactionDisabled}
              title={
                !connected
                  ? "收集前请连接"
                  : interactionDisabled
                    ? "请等待当前仪器交互完成"
                    : undefined
              }
            >
              {collecting ? (
                <LoaderCircle className="animate-spin" size={14} />
              ) : (
                <Database size={14} />
              )}
              收集 Collect
            </button>
          </div>
        )}
      </header>
      {results.length > 0 && (
        <div className="flex flex-wrap items-center gap-[7px] border-t border-line px-3 py-[9px]">
          <span className="mr-[3px] text-xs font-extrabold tracking-[0.05em] text-text-dim uppercase">
            结果
          </span>
          {results.map((acquisitionResult) => (
            <span
              className="inline-flex items-baseline gap-[7px] rounded-full border border-line px-[7px] py-1 text-xs text-text-soft"
              key={acquisitionResult.id}
            >
              {acquisitionResult.label ?? titleCase(acquisitionResult.id)}
              <small className="text-xs text-text-dim">
                {acquisitionResult.dtype}
                {acquisitionResult.unit ? ` · ${acquisitionResult.unit}` : ""}
              </small>
            </span>
          ))}
        </div>
      )}
      {result && <CollectPreview receipt={result} />}
    </section>
  );
}

function PropertyEditor({
  property,
  observation,
  draft,
  editable,
  onChange,
}: {
  property: InstrumentProperty;
  observation?: InstrumentStateObservation;
  draft?: DraftValue;
  editable: boolean;
  onChange: (draft?: DraftValue) => void;
}) {
  const dirty = draft !== undefined;
  return (
    <Controls.Field
      className={classes(
        editorRow,
        dirty && "bg-accent-soft shadow-[inset_2px_0_var(--color-accent)]",
      )}
      data-testid={`interface-property-${property.id}`}
    >
      <span className={propertyLabel}>
        <span className="grid min-w-0">
          <strong
            className="overflow-hidden text-xs font-[670] text-ellipsis whitespace-nowrap"
            data-testid="property-label"
          >
            {property.label ?? titleCase(property.id)}
          </strong>
        </span>
        <small className="flex-none text-xs font-extrabold tracking-[0.04em] text-text-dim uppercase">
          {accessLabel(property.access)}
        </small>
      </span>
      <InstrumentPropertyInput
        property={property}
        currentValue={observation?.value}
        draft={draft}
        editable={editable}
        onChange={onChange}
      />
      {property.description && (
        <small className={propertyDescription}>{property.description}</small>
      )}
      {observation && (
        <small className={propertyDescription}>
          观测来源 {titleCase(observation.source)}
          {observation.observed_at
            ? ` · ${new Date(observation.observed_at).toLocaleString()}`
            : ""}
        </small>
      )}
      {dirty && (
        <button type="button" className={propertyReset} onClick={() => onChange()}>
          重置暂存值
        </button>
      )}
    </Controls.Field>
  );
}

function CollectPreview({ receipt }: { receipt: InstrumentCollectReceipt }) {
  const trace = traceValues(receipt);
  const unavailable = unavailableResults(receipt);
  return (
    <div className="grid gap-[9px] border-t border-line bg-bg px-3 py-[11px]">
      <div className="flex items-baseline gap-[9px]">
        <strong
          className={classes(
            "text-xs text-accent",
            (receipt.status === "not_collected" || receipt.status === "unknown") && "text-yellow",
          )}
        >
          Collect 回执： {titleCase(receipt.status)}
        </strong>
        {receipt.readback && (
          <small className="text-xs text-text-dim">
            {Object.keys(receipt.readback.values ?? {}).length} 项返回结果
          </small>
        )}
      </div>
      {unavailable.count > 0 && (
        <div className="flex items-center gap-[7px] text-yellow" role="status">
          <AlertTriangle size={13} aria-hidden="true" />
          <span className="text-xs font-[650]">{unavailable.count} 项结果不可用</span>
          <small className="text-inherit">
            原因： {unavailable.reasons.map(titleCase).join(", ")}
          </small>
        </div>
      )}
      {trace && <TracePreview values={trace} />}
    </div>
  );
}

function requiresPayload(operation: InstrumentOperation): boolean {
  return (operation.arguments ?? []).some((argument) => argument.value_type.type === "payload");
}

function PreparedOperation({ operation }: { operation: InstrumentOperation }) {
  return (
    <section className="border-t border-line px-3 py-2.5">
      <strong className="text-xs text-text-soft">
        {operation.label ?? titleCase(operation.id)}
      </strong>
      {operation.description && <p className={controlDescription}>{operation.description}</p>}
      <p className={controlDescription}>
        需要作者工作流：此 operation 需要已准备的 payload，本面板不能直接 Invoke。
      </p>
      <details className="mt-2 text-xs text-text-dim">
        <summary className="cursor-pointer">如何准备与调用</summary>
        <p className={controlDescription}>
          在作者 Python 程序中按驱动契约准备 payload，再通过已有的类型化客户端或实验准备流程调用。
          使用 Python 手动会话前，先结束本页会话并等待其他运行释放设备；资源 lease 检查仍然适用。
        </p>
        <AuthorWorkflowLinks />
      </details>
    </section>
  );
}

export function AuthorWorkflowLinks() {
  return (
    <p className="flex flex-wrap gap-3 text-xs">
      <a
        className="text-accent underline"
        href="https://scopecat-project.github.io/scopecat/how-to/control-instruments/#use-a-configured-device-now"
        target="_blank"
        rel="noreferrer"
      >
        Python 设备会话（lab.devices.open）
      </a>
      <a
        className="text-accent underline"
        href="https://scopecat-project.github.io/scopecat/how-to/control-instruments/#return-from-manual-work-to-an-experiment"
        target="_blank"
        rel="noreferrer"
      >
        作者程序准备与提交
      </a>
    </p>
  );
}

function TracePreview({ values }: { values: number[] }) {
  if (values.length === 0) return null;
  const width = 420;
  const height = 100;
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const range = maximum - minimum || 1;
  const points = values
    .map((value, index) => {
      const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * width;
      const y = height - ((value - minimum) / range) * (height - 8) - 4;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg
      className="h-[110px] w-full rounded-sm border border-line p-[5px] [background-image:linear-gradient(var(--color-line)_1px,transparent_1px),linear-gradient(90deg,var(--color-line)_1px,transparent_1px)] [background-size:25%_50%] [&_polyline]:fill-none [&_polyline]:stroke-accent [&_polyline]:[stroke-width:1.5] [&_polyline]:[vector-effect:non-scaling-stroke]"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`${values.length} 点轨迹预览`}
      preserveAspectRatio="none"
    >
      <polyline points={points} />
    </svg>
  );
}

export function InspectorEmpty({
  icon,
  title,
  detail,
}: {
  icon: React.ReactNode;
  title: string;
  detail: string;
}) {
  return (
    <div className="grid min-h-[230px] place-content-center justify-items-center px-5 py-[35px] text-center text-text-dim">
      <span
        className="mb-[11px] grid size-[42px] place-items-center rounded-full bg-panel-strong"
        aria-hidden="true"
      >
        {icon}
      </span>
      <h3 className="m-0 text-sm text-text-soft">{title}</h3>
      <p className="mt-1.5 mb-0 max-w-[370px] text-xs leading-normal">{detail}</p>
    </div>
  );
}

export function AvailabilityBadge({
  availability,
  connected = false,
}: {
  availability: InstrumentView["availability"];
  connected?: boolean;
}) {
  const label = connected
    ? "已连接"
    : { available: "可用", active: "使用中", quarantined: "已隔离", unavailable: "不可用" }[
        availability
      ];
  const tone = connected ? availabilityTone.connected : availabilityTone[availability];
  return (
    <span
      className={classes(
        "inline-flex min-h-[21px] flex-none items-center gap-[5px] rounded-full border border-line px-1.5 text-xs font-extrabold tracking-[0.04em] text-text-dim uppercase",
        tone,
      )}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
      {label}
    </span>
  );
}

export const inlineError =
  "flex items-center gap-2 rounded-md border border-[rgb(215_126_121_/_25%)] bg-red-soft px-3 py-2.5 text-xs leading-normal text-red";
export const problems =
  "rounded-md border border-[rgb(215_126_121_/_25%)] bg-red-soft px-3 py-2.5 text-xs leading-normal text-red";
export const receiptStyle =
  "m-0 rounded-sm border border-line bg-panel-soft px-2.5 py-2 text-xs text-text-soft";
const controlGrid =
  "grid grid-cols-[repeat(2,minmax(230px,1fr))] max-[1100px]:grid-cols-[minmax(0,1fr)]";
const controlHeader = "flex items-start justify-between gap-3.5 px-3 py-2.5 max-[460px]:flex-col";
const controlDescription = "mt-1.5 mb-0 max-w-[760px] text-xs leading-[1.45] text-text-dim";
const editorRow =
  "relative grid min-h-[78px] grid-cols-[minmax(130px,1fr)_minmax(140px,0.85fr)] items-center gap-x-3.5 gap-y-[9px] border-r border-b border-line bg-transparent px-[13px] py-2.5 even:border-r-0 max-[1100px]:border-r-0 max-[680px]:grid-cols-[minmax(120px,1fr)_minmax(125px,0.9fr)] max-[460px]:grid-cols-[minmax(0,1fr)]";
const propertyLabel = "flex min-w-0 items-start justify-between gap-2";
const controlInput = "w-full";
const unitAddon =
  "grid min-w-[42px] place-items-center rounded-r-sm border border-l-0 border-line bg-panel-strong px-2 text-xs text-text-dim";
const propertyDescription = "col-span-full text-xs leading-[1.4] text-text-dim";
const propertyReset =
  "col-span-full justify-self-end cursor-pointer border-0 bg-transparent px-1 py-1 text-xs text-accent underline";
const positiveReceipt = "border-[rgb(128_163_207_/_24%)] bg-accent-soft text-accent";
const warningReceipt = "border-[rgb(207_173_104_/_25%)] bg-yellow-soft text-yellow";
const availabilityTone = {
  available: positiveReceipt,
  connected: positiveReceipt,
  active: positiveReceipt,
  quarantined: warningReceipt,
  unavailable: "border-[rgb(215_126_121_/_25%)] bg-red-soft text-red",
} satisfies Record<InstrumentView["availability"] | "connected", string>;

export function receiptTone(status: string): string {
  return ["applied", "unchanged", "invoked"].includes(status) ? positiveReceipt : warningReceipt;
}

export function instrumentStateTargets(
  description: InstrumentDescription | undefined,
): Map<string, InstrumentStateTarget> {
  const targets = new Map<string, InstrumentStateTarget>();
  if (!description) return targets;
  for (const instrumentInterface of description.interfaces ?? []) {
    for (const mountPath of interfaceMountPaths(description, instrumentInterface.id)) {
      collectInterfaceTargets(targets, instrumentInterface.id, mountPath, instrumentInterface);
    }
  }
  for (const deviceState of description.device_schemas ?? []) {
    for (const member of deviceState.members ?? []) {
      const target = deviceStateTarget(
        deviceState.id,
        member.component_path ?? [],
        member.property.id,
      );
      targets.set(stateMemberKey(target), target);
    }
  }
  return targets;
}

function collectInterfaceTargets(
  targets: Map<string, InstrumentStateTarget>,
  interfaceId: string,
  componentPath: string[],
  member: InterfaceMember,
): void {
  for (const property of member.properties ?? []) {
    const target = interfaceStateTarget(interfaceId, componentPath, property.id);
    targets.set(stateMemberKey(target), target);
  }
  for (const child of member.components ?? []) {
    collectInterfaceTargets(targets, interfaceId, [...componentPath, child.id], child);
  }
}

export function filterDrafts(
  drafts: Record<string, DraftValue>,
  declaredTargets: Map<string, InstrumentStateTarget>,
): Record<string, DraftValue> {
  const next: Record<string, DraftValue> = {};
  let changed = false;
  for (const [key, draft] of Object.entries(drafts)) {
    if (declaredTargets.has(key)) next[key] = draft;
    else changed = true;
  }
  return changed ? next : drafts;
}

export function stateObservation(
  state: InstrumentState | undefined,
  target: InstrumentStateTarget,
): InstrumentStateObservation | undefined {
  return (state?.observations ?? []).find(
    (observation) => stateMemberKey(observation.target) === stateMemberKey(target),
  );
}

type NumericOperationArgumentType = Extract<
  NonNullable<InstrumentOperation["arguments"]>[number]["value_type"],
  { type: "int" | "float" | "quantity" }
>;

function operationArgumentDraft(
  type: NumericOperationArgumentType,
  raw: string,
  unit?: string,
): DraftValue {
  const number = Number(raw);
  const valid =
    raw.length > 0 &&
    Number.isFinite(number) &&
    (type.type !== "int" || Number.isInteger(number)) &&
    (type.minimum === null || type.minimum === undefined || number >= type.minimum) &&
    (type.maximum === null || type.maximum === undefined || number <= type.maximum);
  const resolvedUnit = type.type === "quantity" ? (type.unit ?? unit?.trim()) : undefined;
  const value =
    valid && type.type === "quantity" && resolvedUnit
      ? { value: number, unit: resolvedUnit }
      : valid && type.type !== "quantity"
        ? number
        : undefined;
  return {
    raw,
    unit: type.type === "quantity" ? (type.unit ?? unit ?? "") : undefined,
    value,
  };
}

export function stagedInstrumentMembers(
  drafts: Record<string, DraftValue>,
  declaredTargets: Map<string, InstrumentStateTarget>,
): StagedInstrumentMember[] {
  return Object.entries(drafts).flatMap(([key, draft]) => {
    const target = declaredTargets.get(key);
    if (draft.value === undefined || !target) return [];
    return [
      {
        target,
        value: draft.value,
      },
    ];
  });
}

export function interfaceStateTarget(
  interfaceId: string,
  componentPath: string[],
  propertyId: string,
): InstrumentStateTarget {
  return {
    kind: "interface",
    interface_id: interfaceId,
    component_path: componentPath,
    property_id: propertyId,
  };
}

export function deviceStateTarget(
  schemaId: string,
  componentPath: string[],
  propertyId: string,
): InstrumentStateTarget {
  return {
    kind: "device",
    schema_id: schemaId,
    component_path: componentPath,
    property_id: propertyId,
  };
}

export function stateMemberKey(target: InstrumentStateTarget): string {
  return target.kind === "interface"
    ? [target.kind, target.interface_id, ...target.component_path, target.property_id].join(
        "\u0000",
      )
    : [target.kind, target.schema_id, ...target.component_path, target.property_id].join("\u0000");
}

export function interfaceMountPaths(
  description: InstrumentDescription,
  interfaceId: string,
): string[][] {
  const mounts = (description.interface_mounts ?? [])
    .filter((mount) => mount.interface_id === interfaceId)
    .map((mount) => mount.component_path);
  return mounts.length > 0 ? mounts : [[]];
}

export function resolveInterfaceProperty(
  description: InstrumentDescription,
  target: InstrumentStateTarget,
  property: InstrumentProperty,
): InstrumentProperty {
  if (target.kind !== "interface") return property;
  const implementation = (description.interface_property_implementations ?? []).find(
    (candidate) =>
      candidate.property.interface_id === target.interface_id &&
      samePath(candidate.property.component_path ?? [], target.component_path) &&
      candidate.property.property_id === target.property_id,
  );
  if (!implementation) return property;
  return {
    ...property,
    access: implementation.access,
    capture: implementation.capture,
    restore: implementation.restore,
    value_type: implementation.value_type ?? property.value_type,
  };
}

export function acquisitionKey(target: InstrumentAcquisitionTarget): string {
  return [target.interfaceId, ...target.componentPath, target.acquisition.id].join("\u0000");
}

export function operationKey(target: InstrumentOperationTarget): string {
  return [target.interfaceId, ...target.componentPath, target.operation.id].join("\u0000");
}

function accessLabel(access: InstrumentProperty["access"]): string {
  if (access === "read_only") return "只读";
  if (access === "write_only") return "只写";
  return "读写";
}

function samePath(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function interfaceFallbackLabel(interfaceId: string): string {
  const qualifiedName = interfaceId.slice(0, interfaceId.lastIndexOf("/"));
  return titleCase(qualifiedName.slice(qualifiedName.lastIndexOf(".") + 1));
}

export function requireSession(session: InstrumentSession | undefined): InstrumentSession {
  if (!session) throw new Error("交互前请先连接仪器。");
  return session;
}

export function mutationError(error: unknown): string | undefined {
  return error === null || error === undefined ? undefined : errorMessage(error);
}

export function configuredDefaultsReceiptMessage(
  receipt: InstrumentConfiguredDefaultsApplyReceipt,
): string {
  if (receipt.status === "applied") return "已应用配置的默认值。";
  if (receipt.status === "unchanged") {
    return "状态已与配置的默认值一致。";
  }
  return `已拒绝配置的默认值： ${receipt.problems.map((problem) => problem.message).join(" ")}`;
}

function traceValues(receipt: InstrumentCollectReceipt): number[] | undefined {
  for (const value of Object.values(receipt.readback?.values ?? {})) {
    if (value.kind !== "array") continue;
    const samples = value.values
      .map((sample) => {
        if (typeof sample === "number" && Number.isFinite(sample)) return sample;
        if (
          typeof sample === "object" &&
          sample !== null &&
          "real" in sample &&
          "imag" in sample &&
          typeof sample.real === "number" &&
          typeof sample.imag === "number"
        ) {
          return Math.hypot(sample.real, sample.imag);
        }
        return undefined;
      })
      .filter((sample): sample is number => sample !== undefined);
    if (samples.length > 0) return samples;
  }
  return undefined;
}

function unavailableResults(receipt: InstrumentCollectReceipt): {
  count: number;
  reasons: string[];
} {
  const values = Object.values(receipt.readback?.values ?? {});
  const unavailable = values.filter((value) => value.kind === "unavailable");
  return {
    count: unavailable.length,
    reasons: [...new Set(unavailable.map((value) => value.reason))].sort(),
  };
}

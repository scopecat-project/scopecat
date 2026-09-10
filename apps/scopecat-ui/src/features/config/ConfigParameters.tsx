import { scientificChange, scientificChangeLabel } from "./quantity-change";
import { useMemo, useState } from "react";
import { Braces, CircleDot, GitCompareArrows, Search, Table2 } from "lucide-react";
import {
  diffConfigParameters,
  parameterAtomLabel,
  parameterTypeLabel,
  type ParameterDiff,
  type TableRowDiff,
} from "./config-diff";
import type {
  ConfigProfileSnapshot,
  ParameterAtom,
  ParameterDefinition,
  StoredParameterValue,
  TableParameterType,
  TableParameterValue,
} from "../../api-contract";
import { classes } from "../../ui/styles";

export function ConfigParameters({
  config,
  activeConfig,
  headingId = "parameters-heading",
}: {
  config: ConfigProfileSnapshot;
  activeConfig?: ConfigProfileSnapshot;
  headingId?: string;
}) {
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string>();
  const diffs = useMemo(
    () => diffConfigParameters(activeConfig ?? config, config),
    [activeConfig, config],
  );
  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    if (!query) return diffs;
    return diffs.filter((diff) => {
      const definition = diff.afterDefinition ?? diff.beforeDefinition;
      return [diff.parameterId, definition?.description, definition?.value_type.shape]
        .filter((value): value is string => value !== undefined)
        .join(" ")
        .toLocaleLowerCase()
        .includes(query);
    });
  }, [diffs, search]);
  const selected = filtered.find((diff) => diff.parameterId === selectedId) ?? filtered[0];
  const changedCount = diffs.filter((diff) => diff.status !== "unchanged").length;

  return (
    <section
      className="mt-[13px] overflow-hidden rounded-[10px] border border-line bg-panel-soft"
      aria-labelledby={headingId}
    >
      <header className="flex min-h-[63px] items-center justify-between border-b border-line px-3.5 py-3">
        <div className="flex min-w-0 items-center gap-[9px]">
          <span
            className="grid size-[31px] flex-none place-items-center rounded-[8px] border border-[rgb(120_184_255_/_20%)] bg-blue-soft text-blue"
            aria-hidden="true"
          >
            <Table2 size={17} />
          </span>
          <span className="grid min-w-0">
            <h4 className="m-0 text-[0.75rem]" id={headingId}>
              Parameters
            </h4>
            <small className="overflow-hidden text-[0.56rem] text-ellipsis whitespace-nowrap text-text-dim">
              {config.system.parameter_catalog.id}
            </small>
          </span>
        </div>
        {activeConfig && (
          <span
            className={classes(
              "inline-flex flex-none items-center gap-[5px] rounded-md border px-[7px] py-1 text-[0.55rem] font-[750]",
              changedCount === 0
                ? "border-[rgb(128_163_207_/_18%)] bg-accent-soft text-accent"
                : "border-[rgb(237_201_111_/_20%)] bg-yellow-soft text-yellow",
            )}
          >
            <GitCompareArrows size={13} aria-hidden="true" />
            {changedCount === 0 ? "No parameter differences" : `${changedCount} changed`}
          </span>
        )}
      </header>

      <div className="grid min-h-[365px] grid-cols-[minmax(180px,220px)_minmax(0,1fr)] max-[1100px]:grid-cols-[minmax(165px,190px)_minmax(0,1fr)] max-[880px]:grid-cols-[minmax(180px,220px)_minmax(0,1fr)] max-[680px]:block">
        <aside
          className="min-w-0 border-r border-line bg-[rgb(255_255_255_/_1%)] px-[9px] py-2.5 max-[680px]:border-r-0 max-[680px]:border-b"
          aria-label="Parameters"
        >
          <label className="flex min-h-[34px] items-center gap-[7px] rounded-[7px] border border-line bg-bg px-[9px] text-text-dim focus-within:border-[rgb(128_163_207_/_45%)]">
            <Search size={14} aria-hidden="true" />
            <span className="sr-only">Find parameter</span>
            <input
              className="w-full min-w-0 border-0 bg-transparent p-0 text-[0.64rem] text-text outline-0"
              type="search"
              value={search}
              placeholder="Find parameter"
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <div className="mt-2 grid max-h-[330px] gap-1 overflow-auto [scrollbar-color:#344252_transparent] [scrollbar-width:thin] max-[680px]:max-h-none max-[680px]:grid-flow-col max-[680px]:auto-cols-[minmax(165px,60vw)] max-[680px]:overflow-x-auto">
            {filtered.length === 0 ? (
              <ParameterEmpty
                title="No matching parameters"
                detail="Try a parameter id, description, or shape."
              />
            ) : (
              filtered.map((diff) => {
                const definition = diff.afterDefinition ?? diff.beforeDefinition;
                return (
                  <button
                    key={diff.parameterId}
                    type="button"
                    className={classes(
                      "flex min-h-[51px] min-w-0 cursor-pointer items-center justify-between gap-[7px] rounded-[7px] border border-transparent bg-transparent p-2 text-left text-text hover:border-line hover:bg-panel-strong",
                      diff.parameterId === selected?.parameterId &&
                        "border-[rgb(128_163_207_/_22%)] bg-panel-strong",
                    )}
                    onClick={() => setSelectedId(diff.parameterId)}
                    aria-current={diff.parameterId === selected?.parameterId ? "true" : undefined}
                  >
                    <span className="grid min-w-0 gap-[3px]">
                      <strong className="overflow-hidden text-[0.65rem] text-ellipsis whitespace-nowrap">
                        {diff.parameterId}
                      </strong>
                      <small className="overflow-hidden text-[0.53rem] text-ellipsis whitespace-nowrap text-text-dim">
                        {parameterTypeLabel(definition)}
                      </small>
                    </span>
                    {diff.status !== "unchanged" && <DiffBadge status={diff.status} />}
                  </button>
                );
              })
            )}
          </div>
        </aside>

        <div className="min-w-0 p-4 max-[680px]:p-[13px]">
          {selected ? (
            <ParameterDetail diff={selected} comparing={activeConfig !== undefined} />
          ) : (
            <ParameterEmpty
              title="Nothing selected"
              detail="Choose a parameter to inspect its typed value."
            />
          )}
        </div>
      </div>

      <details className="border-t border-line">
        <summary className="flex min-h-[39px] cursor-pointer list-none items-center gap-[7px] px-3.5 text-[0.58rem] font-bold text-text-dim [&::-webkit-details-marker]:hidden">
          <Braces size={15} aria-hidden="true" />
          Advanced · raw snapshot
        </summary>
        <pre className="m-0 max-h-[360px] overflow-auto border-t border-line bg-bg p-[13px] text-[0.55rem] leading-normal text-text-soft">
          {JSON.stringify(config, null, 2)}
        </pre>
      </details>
    </section>
  );
}

function ParameterDetail({ diff, comparing }: { diff: ParameterDiff; comparing: boolean }) {
  const definition = diff.afterDefinition ?? diff.beforeDefinition;
  const value = diff.after ?? diff.before;
  if (!definition || !value) {
    return (
      <ParameterEmpty
        title={definition ? "Unknown parameter" : "Incomplete parameter"}
        detail={
          definition
            ? `${diff.parameterId} · ${parameterTypeLabel(definition)}. No value has been assigned.`
            : "The catalog definition is not available."
        }
      />
    );
  }
  return (
    <>
      <header className="flex items-start justify-between gap-3 border-b border-line pb-[13px]">
        <div>
          <span className="inline-flex rounded-[5px] border border-[rgb(120_184_255_/_18%)] bg-blue-soft px-[5px] py-[3px] text-[0.5rem] font-extrabold text-blue uppercase">
            {value.shape}
          </span>
          <h5 className="mt-[7px] mb-[3px] text-[0.9rem]">{diff.parameterId}</h5>
          <p className="m-0 text-[0.6rem] leading-[1.45] text-text-dim">
            {definition.description ?? "No parameter description."}
          </p>
        </div>
        {comparing && <DiffBadge status={diff.status} />}
      </header>
      <div className="flex flex-wrap gap-[22px] py-[11px]">
        <ParameterFact label="Type" value={parameterTypeLabel(definition)} />
        {definition.value_type.shape === "table" && (
          <>
            <ParameterFact
              label="Rows"
              value={String(value.shape === "table" ? (value.rows?.length ?? 0) : 0)}
            />
            <ParameterFact
              label="Primary key"
              value={(definition.value_type.primary_key ?? []).join(", ") || "None"}
            />
          </>
        )}
      </div>
      {diff.status === "schema-changed" && (
        <div className="mb-[9px] rounded-[7px] border border-[rgb(255_140_136_/_18%)] bg-red-soft px-[9px] py-2 text-[0.58rem] leading-[1.45] text-red">
          The parameter schema changed. Values are shown without a cell-level comparison.
        </div>
      )}
      <ParameterValueView diff={diff} definition={definition} value={value} />
    </>
  );
}

function ParameterValueView({
  diff,
  definition,
  value,
}: {
  diff: ParameterDiff;
  definition: ParameterDefinition;
  value: StoredParameterValue;
}) {
  if (value.shape === "scalar") {
    const before = diff.before?.shape === "scalar" ? diff.before.value : undefined;
    const after = diff.after?.shape === "scalar" ? diff.after.value : undefined;
    return (
      <div className="grid min-h-[118px] place-items-center rounded-[8px] border border-line bg-bg">
        {diff.status !== "unchanged" && before !== undefined && after !== undefined ? (
          <div>
            <ValueComparison before={before} after={after} />
            <small>{scientificChangeLabel(scientificChange(before, after))}</small>
          </div>
        ) : (
          <ParameterAtomView value={value.value} prominent />
        )}
      </div>
    );
  }
  const valueType = definition.value_type.shape === "table" ? definition.value_type : undefined;
  if (!valueType) {
    return (
      <ParameterEmpty
        title="Table schema unavailable"
        detail="The stored table does not have a matching catalog definition."
      />
    );
  }
  return <TableValueView diff={diff} value={value} valueType={valueType} />;
}

function ValueComparison({ before, after }: { before: ParameterAtom; after: ParameterAtom }) {
  return (
    <div
      className="grid w-[min(100%,440px)] grid-cols-[minmax(90px,1fr)_auto_minmax(90px,1fr)] items-center gap-3.5 max-[680px]:gap-2"
      aria-label="Comparison to selected value"
    >
      <span className="grid min-w-0 gap-[5px] text-center">
        <small className="text-[0.52rem] font-extrabold text-text-dim uppercase">Comparison</small>
        <ParameterAtomView value={before} />
      </span>
      <GitCompareArrows className="text-yellow" size={16} aria-hidden="true" />
      <span className="grid min-w-0 gap-[5px] text-center">
        <small className="text-[0.52rem] font-extrabold text-text-dim uppercase">Selected</small>
        <ParameterAtomView value={after} />
      </span>
    </div>
  );
}

function TableValueView({
  diff,
  value,
  valueType,
}: {
  diff: ParameterDiff;
  value: TableParameterValue;
  valueType: TableParameterType;
}) {
  const keyedRows = diff.table?.mode === "keyed" ? diff.table.rows : undefined;
  const rows =
    keyedRows ??
    (value.rows ?? []).map((row, index): TableRowDiff => ({
      identity: String(index),
      status: "unchanged",
      key: {},
      after: row,
      cells: valueType.columns.map(({ id }) => ({
        columnId: id,
        status: "unchanged",
        after: row[id],
      })),
    }));
  return (
    <div>
      {diff.status === "changed" && diff.table?.mode === "complete-replacement" && (
        <div className="mb-[9px] rounded-[7px] border border-[rgb(237_201_111_/_18%)] bg-yellow-soft px-[9px] py-2 text-[0.58rem] leading-[1.45] text-[#d9c48c]">
          This table has no primary key, so it is compared as one complete replacement.
        </div>
      )}
      <div className="max-w-full overflow-auto rounded-[8px] border border-line [scrollbar-color:#344252_transparent] [scrollbar-width:thin]">
        <table className="w-full min-w-max border-collapse bg-bg [&_th]:min-w-[110px] [&_th]:border-r [&_th]:border-b [&_th]:border-line [&_th]:px-2.5 [&_th]:py-2 [&_th]:text-left [&_th]:align-top [&_td]:min-w-[110px] [&_td]:border-r [&_td]:border-b [&_td]:border-line [&_td]:px-2.5 [&_td]:py-2 [&_td]:text-left [&_td]:align-top [&_tr:last-child>*]:border-b-0 [&_tr>*:last-child]:border-r-0">
          <thead>
            <tr>
              {valueType.columns.map((column) => (
                <th
                  className="sticky top-0 z-[1] bg-panel-strong text-[0.56rem] text-text-soft"
                  key={column.id}
                >
                  <span>{column.id}</span>
                  <small className="mt-0.5 block text-[0.48rem] font-medium text-text-dim">
                    {column.value_type.type}
                    {(valueType.primary_key ?? []).includes(column.id) ? " · key" : ""}
                  </small>
                </th>
              ))}
              {keyedRows && <th>Status</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.identity} className={tableRowTone(row.status)}>
                {valueType.columns.map((column) => {
                  const cell = row.cells.find((candidate) => candidate.columnId === column.id);
                  const displayed = row.after?.[column.id] ?? row.before?.[column.id];
                  return (
                    <td key={column.id} className={cell ? tableCellTone(cell.status) : undefined}>
                      <ParameterAtomView value={displayed!} />
                      {cell?.status === "changed" && (
                        <small className="mt-1 block max-w-56 text-[0.48rem] whitespace-normal text-yellow">
                          was {parameterAtomLabel(cell.before)} ·{" "}
                          {scientificChangeLabel(cell.changeKind ?? "physical")}
                        </small>
                      )}
                    </td>
                  );
                })}
                {keyedRows && (
                  <td>
                    <DiffBadge status={row.status} />
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ParameterAtomView({
  value,
  prominent = false,
}: {
  value: ParameterAtom;
  prominent?: boolean;
}) {
  return (
    <code
      className={classes(
        "text-[0.64rem] whitespace-nowrap text-text",
        prominent && "text-[1.02rem] text-accent",
      )}
      data-testid="parameter-atom"
    >
      {parameterAtomLabel(value)}
    </code>
  );
}

function ParameterFact({ label, value }: { label: string; value: string }) {
  return (
    <span className="grid gap-[3px]">
      <small className="text-[0.5rem] font-extrabold tracking-[0.06em] text-text-dim uppercase">
        {label}
      </small>
      <strong className="text-[0.62rem] text-text-soft">{value}</strong>
    </span>
  );
}

function DiffBadge({ status }: { status: ParameterDiff["status"] | TableRowDiff["status"] }) {
  const label = status.replace("-", " ");
  return (
    <span
      className={classes(
        "inline-flex w-max flex-none rounded-[5px] border px-[5px] py-[3px] text-[0.49rem] font-extrabold capitalize",
        diffTone[status],
      )}
    >
      {label}
    </span>
  );
}

function ParameterEmpty({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="flex items-start gap-2 p-3 text-text-dim">
      <CircleDot className="mt-px flex-none" size={16} aria-hidden="true" />
      <span className="grid gap-[3px]">
        <strong className="text-[0.63rem] text-text-soft">{title}</strong>
        <p className="m-0 text-[0.56rem] leading-[1.4]">{detail}</p>
      </span>
    </div>
  );
}

const diffTone: Record<ParameterDiff["status"] | TableRowDiff["status"], string> = {
  unchanged: "border-line bg-panel text-text-dim",
  changed: "border-[rgb(237_201_111_/_22%)] bg-yellow-soft text-yellow",
  "schema-changed": "border-[rgb(237_201_111_/_22%)] bg-yellow-soft text-yellow",
  added: "border-[rgb(128_163_207_/_22%)] bg-accent-soft text-accent",
  removed: "border-[rgb(255_140_136_/_22%)] bg-red-soft text-red",
};

function tableRowTone(status: TableRowDiff["status"]): string | undefined {
  if (status === "added") return "[&_td]:bg-[rgb(128_163_207_/_6%)]";
  if (status === "removed") return "[&_td]:bg-[rgb(255_140_136_/_6%)]";
  if (status === "changed") return "bg-[rgb(237_201_111_/_5%)]";
  return undefined;
}

function tableCellTone(status: TableRowDiff["status"]): string | undefined {
  if (status === "added") return "bg-[rgb(128_163_207_/_6%)]";
  if (status === "removed") return "bg-[rgb(255_140_136_/_6%)]";
  if (status === "changed") return "bg-[rgb(237_201_111_/_5%)]";
  return undefined;
}

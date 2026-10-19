import * as Controls from "../../ui/Controls";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ProgramInspectionQuery, ReviewSession } from "../../api-contract";
import { EChart, type EChartsCoreOption } from "../../ui/EChart";

type PresentedInspection = NonNullable<ReviewSession["latest_result"]>["inspections"][number];
type PresentedPoint = PresentedInspection["content"]["points"][number];
type PresentedFact = PresentedInspection["content"]["facts"][number];
type PresentedProgram = NonNullable<PresentedInspection["content"]["program"]>;
type PresentedLayer = PresentedProgram["layers"][number];
type PresentedNode = PresentedLayer["nodes"][number];
type ProgramNodeLocation = {
  layerId: string;
  nodeId: string;
  label: string;
};

export function CompiledInspectionView({
  inspections,
  emptyTitle = "No target waveform inspection",
  onProgramQuery,
}: {
  inspections: readonly PresentedInspection[];
  emptyTitle?: string;
  onProgramQuery?: (query: ProgramInspectionQuery) => void;
}) {
  if (inspections.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-line p-4 text-center text-xs text-text-dim">
        {emptyTitle}
      </div>
    );
  }
  return (
    <div className="grid gap-3">
      {inspections.map((inspection) => (
        <div
          className="overflow-hidden rounded-md border border-line bg-panel-soft"
          key={inspection.operation_id}
        >
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3.5 py-2.5">
            <div>
              <strong className="text-xs">{inspection.target_id}</strong>
              <div className="mt-0.5 font-mono text-xs text-text-dim">{inspection.artifact_id}</div>
            </div>
            <span className="text-xs text-text-dim">{inspection.content.kind}</span>
          </div>
          <div className="grid gap-3 p-3.5">
            {inspection.content.program && (
              <ProgramInspectionView
                inspection={inspection.content.program}
                onQuery={onProgramQuery}
              />
            )}
            <FactGrid facts={inspection.content.facts} />
            {inspection.content.points.map((point) => (
              <PointInspection key={point.realization_fingerprint} point={point} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function ProgramInspectionView({
  inspection,
  onQuery,
}: {
  inspection: PresentedProgram;
  onQuery?: (query: ProgramInspectionQuery) => void;
}) {
  const [layerId, setLayerId] = useState(inspection.layers.at(-1)?.id ?? "");
  const layer = inspection.layers.find((candidate) => candidate.id === layerId);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const selectedNode = layer?.nodes.find((node) => node.id === selectedNodeId);
  const [textFilter, setTextFilter] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [entityFilter, setEntityFilter] = useState("");
  const [resourceFilter, setResourceFilter] = useState("");
  const [resultFilter, setResultFilter] = useState("");
  const pendingNavigation = useRef<ProgramNodeLocation | null>(null);
  const [navigationHistory, setNavigationHistory] = useState<ProgramNodeLocation[]>([]);

  useEffect(() => {
    const pending = pendingNavigation.current;
    const pendingLayer = pending
      ? inspection.layers.find((candidate) => candidate.id === pending.layerId)
      : undefined;
    const pendingNode = pendingLayer?.nodes.find((candidate) => candidate.id === pending?.nodeId);
    setLayerId((current) => {
      if (pendingLayer) return pendingLayer.id;
      if (inspection.query) return inspection.query.layer_id;
      return inspection.layers.some((candidate) => candidate.id === current)
        ? current
        : (inspection.layers.at(-1)?.id ?? "");
    });
    setSelectedNodeId((current) => {
      if (pending) return pendingNode?.id ?? null;
      if (current == null) return null;
      return inspection.layers.some((candidate) =>
        candidate.nodes.some((node) => node.id === current),
      )
        ? current
        : null;
    });
    if (
      pending &&
      inspection.query?.layer_id === pending.layerId &&
      inspection.query.node_id === pending.nodeId
    ) {
      pendingNavigation.current = null;
    }
    const query = inspection.query;
    setTextFilter(query?.text ?? "");
    setKindFilter(query?.kind ?? "");
    setEntityFilter(query?.entity_id ?? "");
    setResourceFilter(query?.resource_id ?? "");
    setResultFilter(query?.result_id ?? "");
  }, [inspection]);

  const resetFilters = () => {
    setTextFilter("");
    setKindFilter("");
    setEntityFilter("");
    setResourceFilter("");
    setResultFilter("");
  };

  const queryLayer = (targetLayer: PresentedLayer, nodeId?: string) => {
    onQuery?.({
      layer_id: targetLayer.id,
      snapshot_id: inspection.snapshot_id,
      offset: 0,
      limit: targetLayer.page.limit,
      ...(nodeId && { node_id: nodeId }),
    });
  };

  const openLayer = (targetLayer: PresentedLayer) => {
    setLayerId(targetLayer.id);
    setSelectedNodeId(null);
    pendingNavigation.current = null;
    setNavigationHistory([]);
    resetFilters();
    queryLayer(targetLayer);
  };

  const openNode = (targetLayerId: string, targetNodeId: string, remember = true) => {
    const targetLayer = inspection.layers.find((candidate) => candidate.id === targetLayerId);
    if (!targetLayer) return;
    if (remember && layer && selectedNode) {
      setNavigationHistory((current) => [
        ...current,
        { layerId: layer.id, nodeId: selectedNode.id, label: selectedNode.label },
      ]);
    }
    resetFilters();
    setLayerId(targetLayer.id);
    const targetNode = targetLayer.nodes.find((candidate) => candidate.id === targetNodeId);
    if (targetNode) {
      setSelectedNodeId(targetNode.id);
      pendingNavigation.current = null;
      return;
    }
    const target = { layerId: targetLayer.id, nodeId: targetNodeId, label: targetNodeId };
    setSelectedNodeId(null);
    pendingNavigation.current = target;
    queryLayer(targetLayer, targetNodeId);
  };

  const returnToPreviousNode = () => {
    const target = navigationHistory.at(-1);
    if (!target) return;
    setNavigationHistory((current) => current.slice(0, -1));
    openNode(target.layerId, target.nodeId, false);
  };

  const submitQuery = (cursor?: string) => {
    if (!layer || !onQuery) return;
    onQuery({
      layer_id: layer.id,
      snapshot_id: inspection.snapshot_id,
      ...(cursor && { cursor }),
      offset: 0,
      limit: layer.page.limit,
      ...(textFilter.trim() && { text: textFilter.trim() }),
      ...(kindFilter.trim() && { kind: kindFilter.trim() }),
      ...(entityFilter.trim() && { entity_id: entityFilter.trim() }),
      ...(resourceFilter.trim() && { resource_id: resourceFilter.trim() }),
      ...(resultFilter.trim() && { result_id: resultFilter.trim() }),
    });
    setSelectedNodeId(null);
    pendingNavigation.current = null;
    setNavigationHistory([]);
  };

  return (
    <section className="overflow-hidden rounded-md border border-line bg-panel">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2.5">
        <div>
          <strong className="block text-xs">Quantum program</strong>
          <code className="text-xs text-text-dim">{inspection.program_id}</code>
        </div>
        <span className="text-xs text-text-dim">{inspection.dialect_id}</span>
      </header>
      <ProgramLayerOverview
        inspection={inspection}
        onSelect={openLayer}
        selectedLayerId={layer?.id ?? ""}
      />
      {layer && (
        <>
          <ProgramQueryBar
            entity={entityFilter}
            kind={kindFilter}
            onEntityChange={setEntityFilter}
            onKindChange={setKindFilter}
            onResourceChange={setResourceFilter}
            onResultChange={setResultFilter}
            onSubmit={() => submitQuery()}
            resource={resourceFilter}
            result={resultFilter}
            text={textFilter}
            onTextChange={setTextFilter}
          />
          <div className="grid grid-cols-[minmax(230px,0.8fr)_minmax(300px,1.4fr)] max-[760px]:grid-cols-1">
            <ProgramNodeList
              layer={layer}
              selectedNodeId={selectedNodeId}
              onNext={
                layer.page.next_cursor == null
                  ? undefined
                  : () => submitQuery(layer.page.next_cursor ?? undefined)
              }
              onPrevious={
                layer.page.previous_cursor == null
                  ? undefined
                  : () => submitQuery(layer.page.previous_cursor ?? undefined)
              }
              onSelect={setSelectedNodeId}
            />
            <div className="min-w-0 border-l border-line p-3 max-[760px]:border-t max-[760px]:border-l-0">
              {layer.facts.length > 0 && (
                <div className="mb-3">
                  <FactGrid facts={layer.facts} />
                </div>
              )}
              {layer.id === "scheduled" && (
                <ProgramTimeline
                  layer={layer}
                  onSelect={setSelectedNodeId}
                  selectedNodeId={selectedNodeId}
                />
              )}
              <ProgramNodeInspector
                backTarget={navigationHistory.at(-1)}
                inspection={inspection}
                layer={layer}
                node={selectedNode}
                onBack={returnToPreviousNode}
                onNavigate={openNode}
              />
            </div>
          </div>
        </>
      )}
    </section>
  );
}

function ProgramLayerOverview({
  inspection,
  selectedLayerId,
  onSelect,
}: {
  inspection: PresentedProgram;
  selectedLayerId: string;
  onSelect: (layer: PresentedLayer) => void;
}) {
  const largestLayer = Math.max(...inspection.layers.map((candidate) => candidate.node_count), 1);
  const scale = (count: number) =>
    Math.max(4, (Math.log10(count + 1) / Math.log10(largestLayer + 1)) * 100);
  return (
    <div className="border-b border-line bg-panel-soft p-2.5">
      <div className="mb-2 flex items-center justify-between gap-2 text-xs text-text-dim">
        <span>{inspection.layers.length} abstraction levels</span>
        <span>Log-scaled workload · bounded pages</span>
      </div>
      <div
        aria-label="Program layer overview"
        className="grid grid-cols-4 gap-1.5 max-[1100px]:grid-cols-2 max-[560px]:grid-cols-1"
        role="tablist"
      >
        {inspection.layers.map((candidate) => {
          const selected = candidate.id === selectedLayerId;
          const loaded = candidate.page.returned_node_count;
          return (
            <button
              aria-label={`Open ${candidate.label}: ${candidate.node_count.toLocaleString()} nodes`}
              aria-selected={selected}
              className={`group grid min-w-0 cursor-pointer gap-1 rounded border px-2.5 py-2 text-left ${
                selected
                  ? "border-line-strong bg-panel-strong text-text"
                  : "border-line bg-panel text-text-soft hover:border-line-strong"
              }`}
              key={candidate.id}
              onClick={() => onSelect(candidate)}
              role="tab"
              type="button"
            >
              <span className="text-xs font-bold tracking-[0.07em] text-text-dim uppercase">
                {programResolutionLabel(candidate)}
              </span>
              <span className="truncate text-xs font-bold">{candidate.label}</span>
              <span className="flex items-baseline justify-between gap-2">
                <strong className="text-xs">{candidate.node_count.toLocaleString()}</strong>
                <span className="text-xs text-text-dim">{loaded.toLocaleString()} loaded</span>
              </span>
              <span className="h-1 overflow-hidden rounded bg-panel-soft">
                <span
                  className={`block h-full rounded ${selected ? "bg-accent" : "bg-line-strong"}`}
                  style={{ width: `${scale(candidate.node_count)}%` }}
                />
              </span>
              <span className="truncate text-xs text-text-dim">
                {candidate.page.matching_node_count.toLocaleString()} matching
                {candidate.nodes_truncated ? " · paged" : " · complete"}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function programResolutionLabel(layer: PresentedLayer): string {
  if (layer.id === "authored") return "Intent";
  if (layer.id === "logical") return "Bound structure";
  if (layer.id === "scheduled") return "Timed events";
  if (layer.id === "physical") return "Hardware";
  return layer.kind;
}

function ProgramQueryBar({
  text,
  kind,
  entity,
  resource,
  result,
  onTextChange,
  onKindChange,
  onEntityChange,
  onResourceChange,
  onResultChange,
  onSubmit,
}: {
  text: string;
  kind: string;
  entity: string;
  resource: string;
  result: string;
  onTextChange: (value: string) => void;
  onKindChange: (value: string) => void;
  onEntityChange: (value: string) => void;
  onResourceChange: (value: string) => void;
  onResultChange: (value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <form
      className="grid grid-cols-[minmax(150px,1.4fr)_repeat(4,minmax(100px,0.7fr))_auto] gap-1.5 border-b border-line bg-panel-soft p-2 max-[1050px]:grid-cols-2"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <Controls.Input
        aria-label="Program node search"
        className="min-w-0"
        onChange={(event) => onTextChange(event.target.value)}
        placeholder="Search labels and IDs"
        type="search"
        value={text}
      />
      <Controls.Input
        aria-label="Program node kind"
        className="min-w-0"
        onChange={(event) => onKindChange(event.target.value)}
        placeholder="kind"
        value={kind}
      />
      <Controls.Input
        aria-label="Program entity"
        className="min-w-0"
        onChange={(event) => onEntityChange(event.target.value)}
        placeholder="entity"
        value={entity}
      />
      <Controls.Input
        aria-label="Program resource"
        className="min-w-0"
        onChange={(event) => onResourceChange(event.target.value)}
        placeholder="resource"
        value={resource}
      />
      <Controls.Input
        aria-label="Program result"
        className="min-w-0"
        onChange={(event) => onResultChange(event.target.value)}
        placeholder="result"
        value={result}
      />
      <button
        className="cursor-pointer rounded border border-line bg-panel-strong px-3 py-1.5 text-xs font-bold text-text-soft hover:text-text max-[1050px]:col-span-2"
        type="submit"
      >
        Query layer
      </button>
    </form>
  );
}

function ProgramNodeList({
  layer,
  selectedNodeId,
  onSelect,
  onPrevious,
  onNext,
}: {
  layer: PresentedLayer;
  selectedNodeId: string | null;
  onSelect: (id: string) => void;
  onPrevious?: () => void;
  onNext?: () => void;
}) {
  const nodes = new Map(layer.nodes.map((node) => [node.id, node]));
  const depth = (node: PresentedNode) => {
    let current = node.parent_id ? nodes.get(node.parent_id) : undefined;
    let value = 0;
    while (current && value < 12) {
      value += 1;
      current = current.parent_id ? nodes.get(current.parent_id) : undefined;
    }
    return value;
  };
  const pageStart = layer.page.returned_node_count === 0 ? 0 : layer.page.offset + 1;
  const pageEnd = layer.page.offset + layer.page.returned_node_count;
  return (
    <div className="grid min-h-0 grid-rows-[auto_minmax(0,1fr)_auto]">
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2 text-xs text-text-dim">
        <span>
          {layer.page.matching_node_count.toLocaleString()} matching ·{" "}
          {layer.node_count.toLocaleString()} total
        </span>
        <span>
          {pageStart.toLocaleString()}–{pageEnd.toLocaleString()}
        </span>
      </div>
      <div className="max-h-[390px] overflow-auto p-2" aria-label={`${layer.label} nodes`}>
        {layer.nodes.map((node) => {
          const candidateStatus = placementCandidateStatus(node);
          return (
            <button
              aria-pressed={selectedNodeId === node.id}
              className={`flex w-full cursor-pointer items-center gap-2 rounded border-0 px-2 py-1.5 text-left ${
                selectedNodeId === node.id
                  ? "bg-panel-strong text-text"
                  : "bg-transparent text-text-soft hover:bg-panel-soft"
              }`}
              key={node.id}
              onClick={() => onSelect(node.id)}
              style={{ paddingLeft: `${8 + depth(node) * 13}px` }}
              type="button"
            >
              <span className="w-16 shrink-0 truncate font-mono text-xs text-text-dim">
                {node.kind}
              </span>
              <span className="min-w-0 flex-1 truncate text-xs font-semibold">{node.label}</span>
              {candidateStatus && (
                <span
                  className={`rounded px-1.5 py-0.5 text-xs font-bold uppercase ${
                    candidateStatus === "selected"
                      ? "bg-accent-soft text-accent"
                      : "bg-red-soft text-red"
                  }`}
                >
                  {candidateStatus}
                </span>
              )}
              {node.child_count > 0 && (
                <span className="text-xs text-text-dim">{node.child_count}</span>
              )}
            </button>
          );
        })}
        {layer.nodes.length === 0 && (
          <div className="p-4 text-center text-xs text-text-dim">
            No nodes match this layer query.
          </div>
        )}
      </div>
      {(onPrevious || onNext) && (
        <div className="flex items-center justify-between gap-2 border-t border-line p-2">
          <button
            className="cursor-pointer rounded border border-line bg-panel-soft px-2.5 py-1.5 text-xs font-bold text-text-dim disabled:cursor-default disabled:opacity-35"
            disabled={!onPrevious}
            onClick={onPrevious}
            type="button"
          >
            Previous
          </button>
          <span className="text-xs text-text-dim">
            Server-paged · {layer.page.limit.toLocaleString()} per page
          </span>
          <button
            className="cursor-pointer rounded border border-line bg-panel-soft px-2.5 py-1.5 text-xs font-bold text-text-dim disabled:cursor-default disabled:opacity-35"
            disabled={!onNext}
            onClick={onNext}
            type="button"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}

function ProgramTimeline({
  layer,
  selectedNodeId,
  onSelect,
}: {
  layer: PresentedLayer;
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
}) {
  const events = layer.nodes.filter(
    (node) => node.start_seconds != null && node.duration_seconds != null,
  );
  const visibleDuration = Math.max(
    ...events.map((node) => Number(node.start_seconds) + Number(node.duration_seconds)),
    0,
  );
  const declaredDuration = Number(
    layer.facts.find((fact) => fact.id === "duration_seconds")?.value,
  );
  const duration = declaredDuration > 0 ? declaredDuration : visibleDuration;
  const allLanes = Array.from(new Set(events.flatMap((node) => node.entity_ids)));
  const lanes = allLanes.slice(0, 16);
  if (events.length === 0 || duration <= 0) return null;
  return (
    <div className="mb-3 rounded border border-line bg-panel-soft p-2.5">
      <div className="mb-2 flex items-center justify-between text-xs text-text-dim">
        <strong className="text-text-soft">Visible page timeline</strong>
        <span>
          {events.length.toLocaleString()} events · {lanes.length.toLocaleString()}
          {allLanes.length > lanes.length ? `/${allLanes.length.toLocaleString()}` : ""} lanes ·{" "}
          {duration.toExponential(3)} s
        </span>
      </div>
      <div className="grid gap-1">
        {lanes.map((lane) => (
          <div className="grid grid-cols-[70px_minmax(0,1fr)] items-center gap-2" key={lane}>
            <span className="truncate font-mono text-xs text-text-dim">{lane}</span>
            <div className="relative h-4 overflow-hidden rounded bg-panel">
              {events
                .filter((event) => event.entity_ids.includes(lane))
                .map((event) => (
                  <button
                    aria-label={`Select ${event.label} on ${lane}`}
                    aria-pressed={selectedNodeId === event.id}
                    className={`absolute top-0.5 h-3 min-w-[2px] cursor-pointer rounded border-0 ${
                      selectedNodeId === event.id ? "bg-accent" : "bg-accent/70 hover:bg-accent"
                    }`}
                    key={event.id}
                    onClick={() => onSelect(event.id)}
                    style={{
                      left: `${(Number(event.start_seconds) / duration) * 100}%`,
                      width: `${Math.max((Number(event.duration_seconds) / duration) * 100, 0.25)}%`,
                    }}
                    title={event.label}
                    type="button"
                  />
                ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ProgramNodeInspector({
  inspection,
  layer,
  node,
  backTarget,
  onBack,
  onNavigate,
}: {
  inspection: PresentedProgram;
  layer: PresentedLayer;
  node: PresentedNode | undefined;
  backTarget: ProgramNodeLocation | undefined;
  onBack: () => void;
  onNavigate: (layerId: string, nodeId: string) => void;
}) {
  if (!node) {
    return (
      <div className="grid gap-2">
        {backTarget && (
          <button
            className="w-fit cursor-pointer text-xs font-bold text-accent hover:underline"
            onClick={onBack}
            type="button"
          >
            ← Back to {backTarget.label}
          </button>
        )}
        <div className="rounded border border-dashed border-line p-4 text-center text-xs text-text-dim">
          Select a node to inspect its entities, results, timing, and lowering lineage.
        </div>
      </div>
    );
  }
  const incoming = inspection.links.filter(
    (link) => link.target_layer_id === layer.id && link.target_node_id === node.id,
  );
  const outgoing = inspection.links.filter(
    (link) => link.source_layer_id === layer.id && link.source_node_id === node.id,
  );
  const loadedNodes = new Map(layer.nodes.map((candidate) => [candidate.id, candidate]));
  const ancestors: PresentedNode[] = [];
  let parent = node.parent_id ? loadedNodes.get(node.parent_id) : undefined;
  while (parent && ancestors.length < 8) {
    ancestors.unshift(parent);
    parent = parent.parent_id ? loadedNodes.get(parent.parent_id) : undefined;
  }
  const candidateStatus = placementCandidateStatus(node);
  return (
    <div className="grid gap-2.5">
      {backTarget && (
        <button
          className="w-fit cursor-pointer text-xs font-bold text-accent hover:underline"
          onClick={onBack}
          type="button"
        >
          ← Back to {backTarget.label}
        </button>
      )}
      <div>
        {ancestors.length > 0 && (
          <div className="mb-1 truncate text-xs text-text-dim">
            {ancestors.map((ancestor) => ancestor.label).join(" / ")}
          </div>
        )}
        <span className="text-xs font-bold tracking-[0.06em] text-text-dim uppercase">
          {node.kind}
        </span>
        <strong className="mt-1 block text-xs">{node.label}</strong>
        <code className="mt-1 block truncate text-xs text-text-dim">{node.id}</code>
      </div>
      {candidateStatus && (
        <div
          className={`rounded border px-2.5 py-2 text-xs leading-[1.45] ${
            candidateStatus === "selected"
              ? "border-[rgb(128_163_207_/_28%)] bg-accent-soft text-accent"
              : "border-[rgb(215_126_121_/_25%)] bg-red-soft text-red"
          }`}
          role="status"
        >
          <strong className="block">
            {candidateStatus === "selected" ? "Route selected" : "Route rejected"}
          </strong>
          <span>
            {candidateStatus === "selected"
              ? "This candidate satisfies the requested logical signal and configured device route."
              : node.warnings.join(" · ")}
          </span>
        </div>
      )}
      {node.facts.length > 0 && <FactGrid facts={node.facts} />}
      <div className="flex flex-wrap gap-1.5">
        <ReferenceChips ids={node.entity_ids} label="entity" total={node.entity_count} />
        <ReferenceChips ids={node.resource_ids} label="resource" total={node.resource_count} />
        <ReferenceChips ids={node.result_ids} label="result" total={node.result_count} />
      </div>
      {(node.start_seconds != null || node.duration_seconds != null) && (
        <div className="text-xs text-text-dim">
          start {node.start_seconds ?? "—"} s · duration {node.duration_seconds ?? "—"} s
        </div>
      )}
      {(incoming.length > 0 || outgoing.length > 0) && (
        <div className="grid gap-1 rounded border border-line bg-panel-soft p-2 text-xs text-text-dim">
          <strong className="text-text-soft">Lowering lineage</strong>
          {[...incoming, ...outgoing].slice(0, 6).map((link) => {
            const isIncoming = link.target_layer_id === layer.id;
            const targetLayerId = isIncoming ? link.source_layer_id : link.target_layer_id;
            const targetNodeId = isIncoming ? link.source_node_id : link.target_node_id;
            const targetLayer = inspection.layers.find(
              (candidate) => candidate.id === targetLayerId,
            );
            const targetNode = targetLayer?.nodes.find(
              (candidate) => candidate.id === targetNodeId,
            );
            return (
              <button
                aria-label={`Open lineage ${targetLayer?.label ?? targetLayerId} ${targetNode?.label ?? targetNodeId}`}
                className="grid cursor-pointer grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-1.5 rounded border-0 bg-transparent px-1 py-1 text-left text-text-dim hover:bg-panel-strong hover:text-text-soft"
                key={`${link.source_layer_id}:${link.source_node_id}:${link.target_layer_id}:${link.target_node_id}`}
                onClick={() => onNavigate(targetLayerId, targetNodeId)}
                type="button"
              >
                <span>{isIncoming ? "from" : "to"}</span>
                <span className="min-w-0">
                  <strong className="block truncate text-text-soft">
                    {targetLayer?.label ?? targetLayerId}
                  </strong>
                  <code className="block truncate">{targetNode?.label ?? targetNodeId}</code>
                </span>
                <span className="text-xs">{link.relation} →</span>
              </button>
            );
          })}
          {incoming.length + outgoing.length > 6 && (
            <span>+{incoming.length + outgoing.length - 6} more links</span>
          )}
        </div>
      )}
    </div>
  );
}

function placementCandidateStatus(node: PresentedNode): "selected" | "rejected" | undefined {
  if (node.kind === "placement_candidate_selected") return "selected";
  if (node.kind === "placement_candidate_rejected") return "rejected";
  return undefined;
}

function ReferenceChips({
  ids,
  label,
  total,
}: {
  ids: string[];
  label: "entity" | "resource" | "result";
  total: number;
}) {
  const visible = ids.slice(0, 12);
  return (
    <>
      {visible.map((id) => (
        <span className="rounded bg-panel-strong px-2 py-1 text-xs" key={`${label}:${id}`}>
          {label} {id}
        </span>
      ))}
      {total > visible.length && (
        <span className="rounded border border-line px-2 py-1 text-xs text-text-dim">
          +{(total - visible.length).toLocaleString()} {label}s
        </span>
      )}
    </>
  );
}

function PointInspection({ point }: { point: PresentedPoint }) {
  const option = useMemo(() => waveformChartOption(point), [point]);
  return (
    <div className="grid gap-3 rounded-md border border-line bg-panel p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <span className="text-xs font-bold text-text-soft">Physical realization</span>
          <code className="mt-1 block max-w-[620px] truncate text-xs text-text-dim">
            {point.realization_fingerprint}
          </code>
        </div>
        <span className="text-xs text-text-dim">
          {point.waveform_count} waveform{point.waveform_count === 1 ? "" : "s"}
          {point.waveforms_truncated ? "+" : ""}
        </span>
      </div>
      <FactGrid facts={point.facts} />
      {point.waveforms.length > 0 && (
        <EChart
          ariaLabel="Compiled physical waveforms"
          height={320}
          option={option}
          pointCount={Math.max(...point.waveforms.map((waveform) => waveform.samples.length))}
          seriesCount={point.waveforms.length}
          seriesLabels={point.waveforms.map((waveform) => waveform.channel_id)}
        />
      )}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(190px,1fr))] gap-2">
        {point.waveforms.map((waveform) => (
          <div
            className="rounded border border-line bg-panel-soft px-2.5 py-2"
            key={waveform.channel_id}
          >
            <strong className="block truncate text-xs text-text-soft">{waveform.channel_id}</strong>
            <span className="mt-1 block text-xs text-text-dim">
              peak {waveform.peak_abs.toPrecision(4)} · rms {waveform.rms.toPrecision(4)} ·{" "}
              {waveform.source_sample_count.toLocaleString()} samples
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function FactGrid({ facts }: { facts: PresentedFact[] }) {
  return (
    <dl className="m-0 grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-2">
      {facts.map((fact) => (
        <div className="rounded border border-line bg-panel-soft px-2.5 py-2" key={fact.id}>
          <dt className="text-xs font-bold tracking-[0.04em] text-text-dim uppercase">
            {fact.id.replaceAll("_", " ")}
          </dt>
          <dd className="mt-1 ml-0 truncate text-xs font-semibold text-text-soft">
            {formatInspectionValue(fact.value)} {fact.unit ?? ""}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function waveformChartOption(point: PresentedPoint): EChartsCoreOption {
  return {
    animation: false,
    backgroundColor: "transparent",
    grid: { left: 54, right: 18, top: 34, bottom: 42 },
    legend: { type: "scroll", top: 0, textStyle: { color: "#9eabb8", fontSize: 12 } },
    tooltip: { trigger: "axis" },
    xAxis: {
      name: "sample",
      nameLocation: "middle",
      nameGap: 28,
      type: "value",
      axisLabel: { color: "#98a2ab", fontSize: 12 },
      splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
    },
    yAxis: {
      name: "amplitude",
      type: "value",
      axisLabel: { color: "#98a2ab", fontSize: 12 },
      splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
    },
    series: point.waveforms.map((waveform) => ({
      data: waveform.sample_indices.map((index, offset) => [index, waveform.samples[offset]]),
      emphasis: { focus: "series" },
      name: waveform.channel_id,
      showSymbol: false,
      type: "line",
    })),
  };
}

export function formatInspectionValue(value: unknown): string {
  if (typeof value === "object" && value !== null && "value" in value && "unit" in value) {
    return `${String(value.value)} ${String(value.unit)}`;
  }
  if (typeof value === "object" && value !== null && "id" in value) return String(value.id);
  if (typeof value === "string") return value;
  if (value == null || typeof value === "number" || typeof value === "boolean")
    return String(value);
  return JSON.stringify(value);
}

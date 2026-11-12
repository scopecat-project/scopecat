import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { classes, secondaryButton } from "../../ui/styles";
import "./instrument-control-layout.css";

type ControlView = "control" | "configuration" | "code";
const views = [
  ["control", "控制"],
  ["configuration", "连接配置"],
  ["code", "Python 示例"],
] as const;
const defaultSplit = 48;
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

/** Presentation only: all stateful controls and result/save owners stay mounted. */
export function InstrumentControlLayout({
  controls,
  acquisition,
  results,
  configuration,
  code,
  hasResults,
  pendingCount,
}: {
  controls: ReactNode;
  acquisition: ReactNode;
  results: ReactNode;
  configuration?: ReactNode;
  code: ReactNode;
  hasResults: boolean;
  pendingCount: number;
}) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const splitter = useRef<HTMLDivElement>(null);
  const pointer = useRef<number | undefined>(undefined);
  const compactMode = useRef(false);
  const pendingMeasureFrame = useRef<number | undefined>(undefined);
  const lastFocusedPane = useRef<"control" | "results">("control");
  const pendingFocus = useRef<{ element: HTMLElement; pane: "control" | "results" } | undefined>(
    undefined,
  );
  const [width, setWidth] = useState(0);
  const [compact, setCompact] = useState(false);
  const [compactPane, setCompactPane] = useState<"control" | "results">("control");
  const [controlVisible, setControlVisible] = useState(true);
  const [resultsVisible, setResultsVisible] = useState(false);
  const [view, setView] = useState<ControlView>("control");
  const [split, setSplit] = useState(defaultSplit);
  const [dragging, setDragging] = useState(false);

  useLayoutEffect(() => {
    const element = root.current;
    if (!element) return;
    const measure = () => {
      const bounds = element.getBoundingClientRect();
      if (bounds.width <= 0) return; // A hidden parent must not erase the last usable layout.
      setWidth(bounds.width);
      const nextCompact = compactMode.current ? bounds.width < 800 : bounds.width < 760;
      if (nextCompact !== compactMode.current) {
        const active = element.ownerDocument.activeElement;
        if (active instanceof HTMLElement && element.contains(active))
          pendingFocus.current = { element: active, pane: lastFocusedPane.current };
        if (nextCompact) setCompactPane(lastFocusedPane.current);
        compactMode.current = nextCompact;
        setDragging(false);
        setCompact(nextCompact);
      }
      const height = Math.max(320, window.innerHeight - bounds.top - 20);
      element.style.setProperty("--device-workspace-height", `${height}px`);
    };
    measure();
    const handle = splitter.current;
    // Measuring writes our height and can resize the observed parent. Defer that
    // work outside ResizeObserver delivery and coalesce notifications per frame.
    const scheduleMeasure = () => {
      if (pendingMeasureFrame.current !== undefined) return;
      pendingMeasureFrame.current = window.requestAnimationFrame(() => {
        pendingMeasureFrame.current = undefined;
        measure();
      });
    };
    const observer =
      typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(scheduleMeasure);
    observer?.observe(element);
    if (element.parentElement) observer?.observe(element.parentElement);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      if (pendingMeasureFrame.current !== undefined)
        window.cancelAnimationFrame(pendingMeasureFrame.current);
      pendingMeasureFrame.current = undefined;
      window.removeEventListener("resize", measure);
      if (pointer.current !== undefined && handle?.hasPointerCapture(pointer.current))
        handle.releasePointerCapture(pointer.current);
      pointer.current = undefined;
    };
  }, []);

  useLayoutEffect(() => {
    const requested = pendingFocus.current;
    pendingFocus.current = undefined;
    if (!requested || (requested.element.isConnected && !requested.element.closest("[hidden]")))
      return;
    root.current
      ?.querySelector<HTMLButtonElement>(`[data-pane="${requested.pane}"]`)
      ?.focus({ preventScroll: true });
  }, [compact]);

  const showControl = !hasResults || (compact ? compactPane === "control" : controlVisible);
  const showResults = hasResults && (compact ? compactPane === "results" : resultsVisible);
  const both = showControl && showResults;
  const min = width >= 760 ? Math.max(30, (320 / (width - 12)) * 100) : 30;
  const max = width >= 760 ? Math.min(70, 100 - (300 / (width - 12)) * 100) : 70;
  const effectiveSplit = clamp(split, Math.min(min, max), max);

  useEffect(() => {
    if (both) return;
    const element = splitter.current;
    if (pointer.current !== undefined && element?.hasPointerCapture(pointer.current))
      element.releasePointerCapture(pointer.current);
    pointer.current = undefined;
  }, [both]);

  const show = (pane: "control" | "results") => {
    lastFocusedPane.current = pane;
    setCompactPane(pane);
    if (compact) return;
    if (pane === "control") setControlVisible(true);
    else setResultsVisible(true);
  };
  const hide = (pane: "control" | "results") => {
    setDragging(false);
    // Never leave an empty workspace. Collapsing the only visible pane reveals its peer.
    if (pane === "control") {
      setControlVisible(false);
      setResultsVisible(true);
      setCompactPane("results");
      lastFocusedPane.current = "results";
    } else {
      setResultsVisible(false);
      setControlVisible(true);
      setCompactPane("control");
      lastFocusedPane.current = "control";
    }
  };

  return (
    <div
      ref={root}
      className="device-control-layout"
      data-testid="instrument-workbench-layout"
      data-compact={compact}
      data-control-collapsed={!showControl}
      data-collection-collapsed={!showResults}
    >
      {hasResults && (
        <div className="device-pane-switch" role="group" aria-label="显示区域">
          {compact ? (
            <>
              <button
                className={secondaryButton}
                data-pane="control"
                aria-pressed={showControl}
                onClick={() => show("control")}
              >
                控制
              </button>
              <button
                className={secondaryButton}
                data-pane="results"
                aria-pressed={showResults}
                onClick={() => show("results")}
              >
                结果
              </button>
            </>
          ) : (
            <>
              <button
                className={secondaryButton}
                data-pane="control"
                aria-expanded={showControl}
                aria-controls={`${id}-controls`}
                onClick={() => (showControl ? hide("control") : show("control"))}
              >
                {showControl ? "收起控制" : "展开控制"}
              </button>
              <button
                className={secondaryButton}
                data-pane="results"
                aria-expanded={showResults}
                aria-controls={`${id}-results`}
                onClick={() => (showResults ? hide("results") : show("results"))}
              >
                {showResults ? "收起结果" : "展开结果"}
              </button>
            </>
          )}
          {pendingCount > 0 && (
            <span className="text-xs text-text-dim">{pendingCount} 条待保存</span>
          )}
        </div>
      )}
      <div
        className="device-pane-grid"
        data-both={both}
        style={
          {
            "--device-control-split": `${effectiveSplit}fr`,
            "--device-result-split": `${100 - effectiveSplit}fr`,
          } as CSSProperties
        }
      >
        <section
          id={`${id}-controls`}
          hidden={!showControl}
          aria-label="控制工作区"
          className="device-control-pane"
          onFocusCapture={() => {
            lastFocusedPane.current = "control";
          }}
        >
          <nav className="device-view-switch" aria-label="控制工作区页面">
            {views.map(([value, label]) => (
              <button
                key={value}
                className={secondaryButton}
                aria-pressed={view === value}
                aria-controls={`${id}-${value}`}
                onClick={() => setView(value)}
              >
                {label}
              </button>
            ))}
          </nav>
          <MountedScrollPane
            id={`${id}-control`}
            visible={showControl && view === "control"}
            label="设备控制内容"
          >
            {controls}
          </MountedScrollPane>
          <MountedScrollPane
            id={`${id}-configuration`}
            visible={showControl && view === "configuration"}
            label="设备连接配置"
          >
            {configuration ?? (
              <p className="text-sm text-text-dim">
                此设备使用当前配置中的连接。查看配置不会连接或断开设备；编辑连接请先释放控制，再前往设备管理。
              </p>
            )}
          </MountedScrollPane>
          <MountedScrollPane
            id={`${id}-code`}
            visible={showControl && view === "code"}
            label="设备 Python 示例"
          >
            {code}
          </MountedScrollPane>
          {hasResults && <div className="device-acquisition-actions">{acquisition}</div>}
        </section>
        <div
          ref={splitter}
          hidden={!both}
          role="separator"
          aria-label="调整控制与结果区域大小"
          aria-orientation="vertical"
          aria-controls={`${id}-controls ${id}-results`}
          aria-valuemin={Math.round(Math.min(min, max))}
          aria-valuemax={Math.round(max)}
          aria-valuenow={Math.round(effectiveSplit)}
          aria-valuetext={`控制 ${Math.round(effectiveSplit)}%，结果 ${100 - Math.round(effectiveSplit)}%`}
          tabIndex={0}
          className={classes("device-pane-divider", dragging && "is-dragging")}
          onPointerDown={(event) => {
            if (event.button !== 0 || pointer.current !== undefined) return;
            pointer.current = event.pointerId;
            event.currentTarget.setPointerCapture(event.pointerId);
            event.currentTarget.focus();
            setDragging(true);
            event.preventDefault();
          }}
          onPointerMove={(event) => {
            if (pointer.current !== event.pointerId) return;
            const bounds = root.current?.getBoundingClientRect();
            if (bounds && bounds.width > 12)
              setSplit(
                clamp(((event.clientX - bounds.left - 6) / (bounds.width - 12)) * 100, min, max),
              );
          }}
          onPointerUp={(event) => {
            if (pointer.current !== event.pointerId) return;
            if (event.currentTarget.hasPointerCapture(event.pointerId))
              event.currentTarget.releasePointerCapture(event.pointerId);
            pointer.current = undefined;
            setDragging(false);
          }}
          onPointerCancel={(event) => {
            if (pointer.current !== event.pointerId) return;
            pointer.current = undefined;
            setDragging(false);
          }}
          onLostPointerCapture={(event) => {
            if (pointer.current !== event.pointerId) return;
            pointer.current = undefined;
            setDragging(false);
          }}
          onDoubleClick={() => setSplit(defaultSplit)}
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
            event.preventDefault();
            const step = event.shiftKey ? 5 : 2;
            setSplit(
              event.key === "Home"
                ? min
                : event.key === "End"
                  ? max
                  : clamp(effectiveSplit + (event.key === "ArrowRight" ? step : -step), min, max),
            );
          }}
        >
          <span />
        </div>
        <MountedScrollPane
          id={`${id}-results`}
          visible={showResults}
          label="采集结果工作区"
          className="device-results-pane"
          onFocused={() => {
            lastFocusedPane.current = "results";
          }}
        >
          {results}
        </MountedScrollPane>
      </div>
    </div>
  );
}

function MountedScrollPane({
  id,
  visible,
  label,
  className,
  onFocused,
  children,
}: {
  id: string;
  visible: boolean;
  label: string;
  className?: string;
  onFocused?: () => void;
  children: ReactNode;
}) {
  const element = useRef<HTMLDivElement>(null);
  const position = useRef(0);
  useLayoutEffect(() => {
    if (visible && element.current) element.current.scrollTop = position.current;
  }, [visible]);
  // Independent overflow regions intentionally support keyboard scrolling and focus restoration.
  /* oxlint-disable jsx-a11y/no-noninteractive-tabindex */
  return (
    <div
      id={id}
      ref={element}
      hidden={!visible}
      role="region"
      aria-label={label}
      tabIndex={0}
      onFocusCapture={onFocused}
      className={classes("device-pane-scroll", className)}
      onScroll={(event) => {
        if (visible && event.currentTarget.clientHeight > 0)
          position.current = event.currentTarget.scrollTop;
      }}
    >
      {children}
    </div>
  );
}

/* oxlint-enable jsx-a11y/no-noninteractive-tabindex */

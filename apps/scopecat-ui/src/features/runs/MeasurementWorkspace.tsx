import type { ChartInteraction, ChartInteractionStore } from "../../ui/chart-interaction";
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type Dispatch,
  type SetStateAction,
} from "react";
import { classes, secondaryButton } from "../../ui/styles";

const MeasurementInteractions = createContext<ChartInteractionStore | undefined>(undefined);
export const useMeasurementInteractions = () => useContext(MeasurementInteractions);

export interface MeasurementViewSelection {
  schemaKey: string;
  entities: Record<string, string[]>;
  chartId?: string;
}
const MeasurementSelections = createContext<
  | {
      value: MeasurementViewSelection;
      set: Dispatch<SetStateAction<MeasurementViewSelection>>;
    }
  | undefined
>(undefined);
export const useMeasurementSelections = () => useContext(MeasurementSelections);

const MeasurementFocus = createContext<boolean | undefined>(undefined);
export const useMeasurementFocus = () => useContext(MeasurementFocus);

/** Lives outside transient unavailable-data branches, scoped to one store and run. */
export function MeasurementInteractionBoundary({ children }: { children: ReactNode }) {
  const [selection, setSelection] = useState<MeasurementViewSelection>({
    schemaKey: "",
    entities: {},
  });
  const [interactions] = useState<ChartInteractionStore>(() => {
    const entries = new Map<string, ChartInteraction>();
    return {
      read: (key) => entries.get(key),
      write: (key, value) => {
        entries.delete(key);
        entries.set(key, { zoom: value.zoom, selected: value.selected });
        // A run may contain small multiples; keep this transient store bounded.
        if (entries.size > 64) entries.delete(entries.keys().next().value!);
      },
    };
  });
  return (
    <MeasurementSelections value={{ value: selection, set: setSelection }}>
      <MeasurementInteractions value={interactions}>{children}</MeasurementInteractions>
    </MeasurementSelections>
  );
}

/** Keep the same controls mounted when entering/leaving the browser's modal top layer. */
export function MeasurementWorkspace({
  title,
  progress,
  notices,
  children,
}: {
  title: string;
  progress?: ReactNode;
  notices?: ReactNode;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (!focused) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [focused]);

  return (
    <MeasurementFocus value={focused}>
      <dialog
        ref={dialog}
        style={{ display: focused ? "flex" : "block" }}
        role={focused ? "dialog" : "region"}
        aria-modal={focused || undefined}
        aria-label={`${title} measurements`}
        onClose={() => {
          setFocused(false);
          trigger.current?.focus({ preventScroll: true });
        }}
        className={classes(
          "m-0 w-full max-w-none border-0 bg-panel p-0 text-text",
          focused
            ? "fixed inset-0 flex h-dvh max-h-none flex-col gap-2 overflow-auto p-3 backdrop:bg-bg/80"
            : "static block",
        )}
      >
        <div
          className={classes(
            "mb-2 flex-none items-start gap-2",
            focused ? "grid grid-cols-[minmax(0,1fr)_auto]" : "flex flex-wrap",
          )}
        >
          {focused && <strong className="min-w-0 self-center break-words text-sm">{title}</strong>}
          <div className={classes("min-w-0 flex-1 basis-64", focused && "col-span-2 row-start-2")}>
            {progress}
          </div>
          <button
            ref={trigger}
            type="button"
            className={classes(secondaryButton, focused && "col-start-2 row-start-1")}
            onClick={() => {
              if (focused) dialog.current?.close();
              else {
                dialog.current?.showModal();
                setFocused(true);
                trigger.current?.focus();
              }
            }}
          >
            {focused ? "退出聚焦，查看 run 详情" : "聚焦曲线"}
          </button>
        </div>
        <div className="flex-none space-y-2 [&:empty]:hidden" aria-live="polite">
          {notices}
        </div>
        {children}
      </dialog>
    </MeasurementFocus>
  );
}

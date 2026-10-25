import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { classes, secondaryButton } from "../../ui/styles";

const MeasurementFocus = createContext<boolean | undefined>(undefined);
export const useMeasurementFocus = () => useContext(MeasurementFocus);

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
        <div className="mb-2 flex flex-none flex-wrap items-start gap-2">
          {focused && <strong className="w-full text-sm">{title}</strong>}
          <div className="min-w-0 flex-1 basis-64">{progress}</div>
          <button
            ref={trigger}
            type="button"
            className={secondaryButton}
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

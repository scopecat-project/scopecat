import { useEffect, useLayoutEffect, useRef } from "react";

/** Reading position and editable focus belong to this window, not to the draft record. */
export function useWorkspacePosition(view: string) {
  const positions = useRef(
    new Map<
      string,
      { y: number; element?: HTMLElement; start?: number | null; end?: number | null }
    >(),
  );
  const current = useRef(view);
  useEffect(() => {
    const remember = () => {
      const previous = positions.current.get(current.current);
      const active = document.activeElement;
      const element =
        active instanceof HTMLElement && active.closest("main") && !active.closest("[hidden]")
          ? active
          : previous?.element;
      const input =
        element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement
          ? element
          : undefined;
      positions.current.set(current.current, {
        y: window.scrollY,
        element,
        start: input?.selectionStart,
        end: input?.selectionEnd,
      });
    };
    window.addEventListener("scroll", remember, { passive: true });
    document.addEventListener("focusin", remember);
    document.addEventListener("selectionchange", remember);
    return () => {
      window.removeEventListener("scroll", remember);
      document.removeEventListener("focusin", remember);
      document.removeEventListener("selectionchange", remember);
    };
  }, []);
  useLayoutEffect(() => {
    current.current = view;
    const position = positions.current.get(view);
    const previousFocus = document.activeElement;
    const frame = requestAnimationFrame(() => {
      if (document.visibilityState === "hidden") return;
      window.scrollTo({ top: position?.y ?? 0, left: 0 });
      const active = document.activeElement;
      // Drawer/history restoration or a new user action may have already placed
      // focus after this route committed. Reading-position recovery must not
      // override that newer choice.
      if (
        active !== previousFocus &&
        active instanceof HTMLElement &&
        active !== document.body &&
        active.isConnected &&
        !active.closest("[hidden], [inert]") &&
        active.getClientRects().length
      )
        return;
      if (
        position?.element?.isConnected &&
        !position.element.closest("[hidden], [inert]") &&
        position.element.getClientRects().length
      ) {
        position.element.focus({ preventScroll: true });
        if (
          (position.element instanceof HTMLInputElement ||
            position.element instanceof HTMLTextAreaElement) &&
          position.start != null
        )
          position.element.setSelectionRange(position.start, position.end ?? position.start);
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [view]);
}

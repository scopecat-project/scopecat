import { Toast } from "@base-ui/react/toast";
import type { ReactNode } from "react";

// Each native WebView owns its manager. Only explicit, completed actions belong
// here; errors, uncertain outcomes and polling state stay with their owner.
const manager = Toast.createToastManager();

export function showReceipt(title: string): () => void {
  const id = manager.add({ title, priority: "low" });
  return () => manager.close(id);
}

export function ReceiptProvider({ children }: { children: ReactNode }) {
  return (
    <Toast.Provider toastManager={manager} timeout={3500} limit={3}>
      {children}
      <ReceiptViewport />
    </Toast.Provider>
  );
}

function ReceiptViewport() {
  const { toasts } = Toast.useToastManager();
  return (
    <Toast.Portal>
      <Toast.Viewport
        aria-label="通知"
        className="pointer-events-none fixed right-4 bottom-4 z-[60] flex w-[min(360px,calc(100vw-32px))] flex-col gap-2 outline-none"
      >
        {toasts.map((toast) => (
          <Toast.Root
            key={toast.id}
            toast={toast}
            className="pointer-events-auto flex items-center gap-3 rounded-md border border-line-strong bg-panel px-3 py-2 text-sm text-text shadow-lg data-limited:hidden data-ending:hidden"
          >
            <Toast.Content className="min-w-0 flex-1">
              <Toast.Title />
            </Toast.Content>
            <Toast.Close
              aria-label="关闭通知"
              aria-hidden={false}
              className="grid size-7 shrink-0 cursor-pointer place-items-center rounded text-text-dim hover:bg-panel-strong focus-visible:outline-2 focus-visible:outline-accent"
            >
              ×
            </Toast.Close>
          </Toast.Root>
        ))}
      </Toast.Viewport>
    </Toast.Portal>
  );
}

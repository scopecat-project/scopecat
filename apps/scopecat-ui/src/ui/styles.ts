import { twMerge } from "tailwind-merge";

export const primaryButton =
  "inline-flex min-h-9 justify-self-start cursor-pointer items-center justify-center gap-[7px] rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-bg hover:not-disabled:border-accent-hover hover:not-disabled:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-60";

export const dangerButton =
  "inline-flex min-h-9 justify-self-start cursor-pointer items-center justify-center gap-2 rounded-md border border-red bg-red px-3 text-sm font-semibold text-bg hover:not-disabled:brightness-110 disabled:cursor-not-allowed disabled:opacity-60";

export const secondaryButton =
  "inline-flex min-h-9 justify-self-start cursor-pointer items-center justify-center gap-[7px] rounded-md border border-line-strong bg-panel px-3 text-sm font-semibold text-text-soft hover:not-disabled:bg-panel-strong hover:not-disabled:text-text disabled:cursor-not-allowed disabled:opacity-60";

export const iconButton =
  "grid size-9 cursor-pointer place-items-center rounded-sm border border-line bg-panel p-0 text-text-soft hover:not-disabled:border-line-strong hover:not-disabled:bg-panel-strong hover:not-disabled:text-text disabled:cursor-not-allowed disabled:opacity-60";

export const detailCard = "min-w-0 rounded-md border border-line bg-panel-soft p-3.5";

export const countBadge =
  "inline-flex h-[23px] min-w-6 items-center justify-center rounded-md border border-line bg-panel px-[7px] text-xs font-[750] text-text-dim";

export const eyebrow = "mb-[7px] text-xs font-extrabold tracking-[0.14em] text-accent uppercase";

export const dialogBackdrop = "fixed inset-0 z-50 bg-[rgb(3_6_9_/_70%)]";

export const dialogViewport =
  "fixed inset-0 z-51 grid overflow-auto p-5 place-items-center max-[460px]:p-2.5";

export const dialogPopup =
  "w-[min(520px,100%)] overflow-hidden rounded-lg border border-line-strong bg-panel shadow-[0_24px_70px_rgb(0_0_0_/_50%)] outline-0";

export const dialogTitle = "m-0 text-base font-bold text-text";

export const dialogDescription = "mt-[5px] mb-0 text-sm leading-normal text-text-dim";

export function classes(...values: Array<string | false | null | undefined>): string {
  return twMerge(values);
}

// Shared defaults for ordinary forms; chart/table layouts can override widths.
export const formControl =
  "min-h-9 min-w-0 max-w-full rounded-md border border-line-strong bg-panel px-3 py-1.5 text-sm font-normal leading-5 text-text placeholder:text-text-dim focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-60 aria-invalid:border-red aria-invalid:bg-red-soft";
export const fieldLabel = "grid min-w-0 gap-1.5 text-sm leading-5 text-text-soft";
export const pageTitle = "m-0 text-lg font-semibold leading-7 text-text";
export const sectionTitle = "m-0 text-base font-semibold leading-6 text-text";
export const pageContent = "min-w-0 space-y-4 p-4 text-sm leading-6";
// Routine saved/loading messages stay subordinate to fields and blocking errors.
export const notice = "text-sm leading-6 text-text-dim";
export const errorNotice =
  "rounded-md border border-red/50 bg-red-soft px-3 py-2 text-sm leading-6 text-red";

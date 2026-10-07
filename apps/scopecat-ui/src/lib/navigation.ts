import type { MouseEvent } from "react";
export interface NavigationOptions {
  replace?: boolean;
}

/** User navigation adds history; automatic initial selection updates the entry. */
export function navigate(target: URL | string, options: NavigationOptions = {}) {
  const url = new URL(target, window.location.href);
  if (url.href === window.location.href) return;
  const position: number = window.history.state?.scopecatPosition ?? 0;
  if (options.replace) {
    window.history.replaceState({ scopecatPosition: position }, "", url);
  } else {
    window.history.replaceState({ scopecatPosition: position }, "", window.location.href);
    window.history.pushState({ scopecatPosition: position + 1 }, "", url);
  }
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

/** Keep ordinary console links in this window without discarding its editable state. */
export function navigateLink(event: MouseEvent<HTMLAnchorElement>) {
  const link = event.currentTarget;
  const url = new URL(link.href);
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.ctrlKey ||
    event.metaKey ||
    event.shiftKey ||
    event.altKey ||
    link.hasAttribute("download") ||
    (link.target && link.target !== "_self") ||
    url.origin !== window.location.origin ||
    url.pathname !== window.location.pathname
  )
    return;
  event.preventDefault();
  navigate(url);
}

export function navigateBack() {
  // The native host's startup/recovery page is not application navigation.
  if (window.history.state?.scopecatPosition > 0) window.history.back();
}

export function navigateForward() {
  window.history.forward();
}

function subscribeLocation(changed: () => void) {
  window.addEventListener("hashchange", changed);
  window.addEventListener("popstate", changed);
  return () => {
    window.removeEventListener("hashchange", changed);
    window.removeEventListener("popstate", changed);
  };
}

export function useLocationUrl() {
  return new URL(useSyncExternalStore(subscribeLocation, () => window.location.href));
}
import { useSyncExternalStore } from "react";

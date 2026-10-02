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

export function navigateBack() {
  // The native host's startup/recovery page is not application navigation.
  if (window.history.state?.scopecatPosition > 0) window.history.back();
}

export function navigateForward() {
  window.history.forward();
}

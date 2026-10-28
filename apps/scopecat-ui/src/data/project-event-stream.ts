/** Native EventSource retries network failures, but closes permanently after some HTTP errors. */
export function projectEventStream(
  url: () => string,
  onOpen: () => void,
  onProject: (event: Event) => void,
): () => void {
  let source: EventSource;
  let retry: ReturnType<typeof setTimeout> | undefined;
  const detach = () => {
    source.removeEventListener("open", onOpen);
    source.removeEventListener("project", onProject);
    source.removeEventListener("error", onError);
    source.close();
  };
  const onError = () => {
    if (source.readyState !== EventSource.CLOSED || retry !== undefined) return;
    retry = setTimeout(() => {
      retry = undefined;
      detach();
      connect();
    }, 3_000);
  };
  const connect = () => {
    source = new EventSource(url());
    source.addEventListener("open", onOpen);
    source.addEventListener("project", onProject);
    source.addEventListener("error", onError);
  };
  connect();
  return () => {
    if (retry !== undefined) clearTimeout(retry);
    detach();
  };
}

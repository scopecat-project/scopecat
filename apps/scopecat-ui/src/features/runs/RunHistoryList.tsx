import { Component, createRef, type ReactNode } from "react";

interface Props {
  scope: string;
  children: ReactNode;
  className: string;
}
interface Anchor {
  title: string;
  x: number;
  y: number;
}

// Capture before React inserts rows. Native scroll anchoring does not preserve
// the horizontal run browser on narrow screens.
export class RunHistoryList extends Component<Props, object, Anchor | null> {
  private viewport = createRef<HTMLDivElement>();

  override getSnapshotBeforeUpdate(previous: Props): Anchor | null {
    const node = this.viewport.current;
    if (!node || previous.scope !== this.props.scope || (!node.scrollLeft && !node.scrollTop))
      return null;
    const frame = node.getBoundingClientRect();
    for (const row of node.querySelectorAll<HTMLButtonElement>("button[title^='Inspect run ']")) {
      const bounds = row.getBoundingClientRect();
      if (
        bounds.right > frame.left &&
        bounds.left < frame.right &&
        bounds.bottom > frame.top &&
        bounds.top < frame.bottom
      )
        return { title: row.title, x: bounds.x - frame.x, y: bounds.y - frame.y };
    }
    return null;
  }

  override componentDidUpdate(_previous: Props, _state: object, anchor: Anchor | null) {
    const node = this.viewport.current;
    if (!node || !anchor) return;
    const row = [...node.querySelectorAll<HTMLButtonElement>("button[title^='Inspect run ']")].find(
      (item) => item.title === anchor.title,
    );
    if (!row) return;
    const frame = node.getBoundingClientRect();
    const bounds = row.getBoundingClientRect();
    node.scrollLeft += bounds.x - frame.x - anchor.x;
    node.scrollTop += bounds.y - frame.y - anchor.y;
  }

  override render() {
    return (
      <div ref={this.viewport} className={this.props.className}>
        {this.props.children}
      </div>
    );
  }
}

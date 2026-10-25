import { useEffect, useRef, useState } from "react";
import {
  Activity,
  Boxes,
  Cable,
  CircleHelp,
  GitCompareArrows,
  Menu,
  Settings2,
  X,
} from "lucide-react";

export type ProjectView =
  | "launch"
  | "runs"
  | "samples"
  | "history"
  | "analyses"
  | "decisions"
  | "reviews"
  | "instruments"
  | "configuration"
  | "settings"
  | "help";
const groups = [
  {
    label: "实验",
    items: [
      ["launch", "实验", "Experiments"],
      ["decisions", "决策", "Decisions"],
      ["reviews", "波形检查", "Reviews"],
    ],
  },
  {
    label: "数据",
    items: [
      ["history", "数据", "Data"],
      ["runs", "运行详情", "Runs"],
      ["analyses", "分析比较", "Analyses"],
    ],
  },
  { label: "样品", items: [["samples", "样品", "Samples"]] },
  {
    label: "工作条件",
    items: [
      ["instruments", "设备与驱动", "Devices and drivers"],
      ["configuration", "参数与绑定", "Configuration"],
    ],
  },
  {
    label: "应用",
    items: [
      ["settings", "设置", "Settings"],
      ["help", "帮助", "Help"],
    ],
  },
] as const;
const icons = [Activity, Boxes, Boxes, Cable, Settings2];

export function WorkspaceNavigation({
  view,
  onSelect,
  projectName,
  focused,
  onShowNavigation,
  decisionAttention,
}: {
  view: ProjectView;
  onSelect: (view: ProjectView) => void;
  projectName?: string;
  focused: boolean;
  onShowNavigation: () => void;
  decisionAttention?: string;
}) {
  const [compact, setCompact] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const closeWideDrawer = () => {
      if (window.innerWidth > 760) setDrawer(false);
    };
    window.addEventListener("resize", closeWideDrawer);
    return () => window.removeEventListener("resize", closeWideDrawer);
  }, []);
  useEffect(() => {
    document.documentElement.classList.toggle("navigation-compact", compact);
    document.documentElement.classList.toggle("object-focused", focused);
    return () => {
      document.documentElement.classList.remove("navigation-compact", "object-focused");
    };
  }, [compact, focused]);
  useEffect(() => {
    if (!drawer) return;
    const main = document.getElementById("workspace-body");
    const returnFocus = opener.current;
    if (main) main.inert = true;
    panel.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawer(false);
      if (!drawer || event.key !== "Tab") return;
      const buttons = Array.from(
        panel.current?.querySelectorAll<HTMLButtonElement>("button") ?? [],
      ).filter((button) => button.getClientRects().length > 0);
      const first = buttons[0],
        last = buttons.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    const navigation = panel.current;
    navigation?.addEventListener("keydown", handleKey);
    return () => {
      navigation?.removeEventListener("keydown", handleKey);
      if (main) main.inert = false;
      if (window.innerWidth <= 760) returnFocus?.focus();
      else navigation?.querySelector<HTMLButtonElement>('[aria-current="page"]')?.focus();
    };
  }, [drawer]);
  if (focused)
    return (
      <button className="workspace-show-navigation" onClick={onShowNavigation}>
        显示主导航 / Navigation
      </button>
    );
  return (
    <>
      <button
        ref={opener}
        className="workspace-menu"
        aria-label="导航 / Open navigation"
        aria-expanded={drawer}
        aria-controls="workspace-navigation"
        onClick={() => setDrawer(true)}
      >
        <Menu size={17} aria-hidden="true" />
        导航
      </button>
      {drawer && (
        <button
          className="workspace-scrim"
          aria-label="关闭导航遮罩 / Close navigation overlay"
          onClick={() => setDrawer(false)}
        />
      )}
      <div
        id="workspace-navigation"
        ref={panel}
        className={`workspace-sidebar ${drawer ? "is-open" : ""}`}
        role={drawer ? "dialog" : "group"}
        tabIndex={-1}
        aria-modal={drawer ? true : undefined}
        aria-label="Workspace navigation"
      >
        <div className="workspace-brand">
          ◈ <strong>Scopecat</strong>
          <button
            className="workspace-drawer-close"
            aria-label="关闭导航 / Close navigation"
            onClick={() => setDrawer(false)}
          >
            <X size={18} />
          </button>
        </div>
        <nav aria-label="Project sections">
          {groups.map((group, index) => {
            const Icon = icons[index] ?? CircleHelp;
            return (
              <div className="workspace-nav-group" key={group.label}>
                <p>{group.label}</p>
                {group.items.map(([id, label, english], itemIndex) => (
                  <button
                    key={id}
                    type="button"
                    aria-label={`${label} / ${english}`}
                    title={`${label} · ${english}`}
                    aria-current={view === id ? "page" : undefined}
                    className={`workspace-nav-item ${itemIndex > 0 ? "is-child" : ""}`}
                    onClick={() => {
                      setDrawer(false);
                      onSelect(id);
                    }}
                  >
                    {itemIndex === 0 ? (
                      <Icon size={17} aria-hidden="true" />
                    ) : (
                      <GitCompareArrows size={13} aria-hidden="true" />
                    )}
                    <span>
                      {label}
                      <small>{english}</small>
                      {id === "decisions" && decisionAttention && (
                        <small className="workspace-attention" aria-label="Decision attention">
                          {decisionAttention}
                        </small>
                      )}
                    </span>
                  </button>
                ))}
              </div>
            );
          })}
        </nav>
        <div className="workspace-project" title={projectName}>
          {projectName ?? "连接工作空间…"}
        </div>
        <button
          className="workspace-collapse"
          aria-label={compact ? "展开 / Expand navigation" : "收起导航 / Collapse navigation"}
          aria-expanded={!compact}
          onClick={() => setCompact(!compact)}
        >
          {compact ? "› 展开" : "‹ 收起导航"}
        </button>
      </div>
    </>
  );
}

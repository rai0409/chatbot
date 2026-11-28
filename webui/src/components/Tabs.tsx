import { type KeyboardEvent, type ReactNode, useRef, useState } from "react";

export interface Tab {
  id: string;
  label: string;
  content: ReactNode;
}

/** Tabs with the roles a screen reader expects (tablist, tab, tabpanel). Only the open panel is rendered. The arrow
 *  keys, Home and End move between the tabs (one tab stop: the open tab). */
export function Tabs({ tabs, initial }: { tabs: Tab[]; initial?: string }) {
  const [current, setCurrent] = useState(initial ?? tabs[0]?.id);
  const open = tabs.find((tab) => tab.id === current) ?? tabs[0];
  const list = useRef<HTMLDivElement>(null);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const at = tabs.findIndex((tab) => tab.id === open?.id);
    const next =
      event.key === "ArrowRight" ? (at + 1) % tabs.length
      : event.key === "ArrowLeft" ? (at - 1 + tabs.length) % tabs.length
      : event.key === "Home" ? 0
      : event.key === "End" ? tabs.length - 1
      : -1;
    if (next < 0 || !tabs[next]) return;
    event.preventDefault();
    setCurrent(tabs[next].id);
    list.current?.querySelectorAll<HTMLElement>('[role="tab"]')[next]?.focus();
  }

  return (
    <div>
      <div role="tablist" className="tabs" ref={list} onKeyDown={onKeyDown}>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            id={`tab-${tab.id}`}
            aria-selected={tab.id === open?.id}
            aria-controls={`panel-${tab.id}`}
            tabIndex={tab.id === open?.id ? 0 : -1}
            type="button"
            onClick={() => setCurrent(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {open && (
        <div role="tabpanel" id={`panel-${open.id}`} aria-labelledby={`tab-${open.id}`}>
          {open.content}
        </div>
      )}
    </div>
  );
}

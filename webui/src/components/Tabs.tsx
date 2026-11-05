import { type ReactNode, useState } from "react";

export interface Tab {
  id: string;
  label: string;
  content: ReactNode;
}

/** Tabs with the roles a screen reader expects (tablist, tab, tabpanel). Only the open panel is rendered. */
export function Tabs({ tabs, initial }: { tabs: Tab[]; initial?: string }) {
  const [current, setCurrent] = useState(initial ?? tabs[0]?.id);
  const open = tabs.find((tab) => tab.id === current) ?? tabs[0];
  return (
    <div>
      <div role="tablist" className="tabs">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            id={`tab-${tab.id}`}
            aria-selected={tab.id === open?.id}
            aria-controls={`panel-${tab.id}`}
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

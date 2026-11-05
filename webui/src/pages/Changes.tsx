import { useMemo, useState } from "react";
import { overviewApi } from "../api/overview";
import { ErrorState, Loading } from "../components/Feedback";
import { useLoad } from "../lib/useLoad";
import { Tabs } from "../components/Tabs";
import { jaChanges as c } from "../i18n/changes";
import "./changes/changes.css";
import { ReadOnlyContext } from "./changes/common";
import { KnobsTab } from "./changes/KnobsTab";
import { LedgerTab } from "./changes/LedgerTab";
import { MonitoringTab } from "./changes/MonitoringTab";
import { ProfilesTab } from "./changes/ProfilesTab";
import { ProposalsTab } from "./changes/ProposalsTab";

export function ChangesPage() {
  const access = useLoad(overviewApi.access, [], c.loadFailed);
  const [readOnly, setReadOnly] = useState(false);
  // Opening a proposal from another tab remounts the tabs on the proposals tab with that proposal open.
  const [target, setTarget] = useState<{ id: string; n: number } | null>(null);
  const context = useMemo(() => ({ readOnly, markReadOnly: () => setReadOnly(true) }), [readOnly]);
  const open = (id: string) => setTarget((prev) => ({ id, n: (prev?.n ?? 0) + 1 }));

  return (
    <ReadOnlyContext.Provider value={context}>
      <section>
        <h1>{c.title}</h1>
        <p className="muted">{c.intro}</p>
        {access.error ? <ErrorState message={access.error} onRetry={access.reload} /> : !access.data || access.loading ? <Loading /> : (
        <Tabs
          key={target?.n ?? 0}
          initial={target && access.data.you.reach === "installation" ? "proposals" : undefined}
          tabs={[
            ...(access.data.you.reach === "installation" ? [
            { id: "proposals", label: c.tabs.proposals, content: <ProposalsTab initialId={target?.id} /> },
            { id: "profiles", label: c.tabs.profiles, content: <ProfilesTab /> },
            { id: "ledger", label: c.tabs.ledger, content: <LedgerTab onOpenProposal={open} /> },
            { id: "knobs", label: c.tabs.knobs, content: <KnobsTab /> },
            ] : []),
            { id: "monitoring", label: c.tabs.monitoring, content: <MonitoringTab onOpenProposal={open} /> },
          ]}
        />
        )}
      </section>
    </ReadOnlyContext.Provider>
  );
}

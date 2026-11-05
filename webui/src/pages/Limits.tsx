import { Tabs } from "../components/Tabs";
import { jaLimits as j } from "../i18n/limits";
import { AssignmentsTab } from "./limits/AssignmentsTab";
import { LimitsTab } from "./limits/LimitsTab";

export function LimitsPage() {
  return (
    <section>
      <h1>{j.title}</h1>
      <p className="muted">{j.intro}</p>
      <Tabs
        tabs={[
          { id: "limits", label: j.tabLimits, content: <LimitsTab /> },
          { id: "assignments", label: j.tabAssignments, content: <AssignmentsTab /> },
        ]}
      />
    </section>
  );
}

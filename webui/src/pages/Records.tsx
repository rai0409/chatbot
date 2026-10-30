import { Tabs } from "../components/Tabs";
import { jaRecords } from "../i18n/records";
import { LevelSettings } from "./records/LevelSettings";
import { RecordList } from "./records/RecordList";
import { Retention } from "./records/Retention";
import "./records/records.css";

export function RecordsPage() {
  return (
    <section>
      <h1>{jaRecords.title}</h1>
      <p className="muted">{jaRecords.intro}</p>
      <Tabs
        tabs={[
          { id: "list", label: jaRecords.tabs.list, content: <RecordList /> },
          { id: "level", label: jaRecords.tabs.level, content: <LevelSettings /> },
          { id: "retention", label: jaRecords.tabs.retention, content: <Retention /> },
        ]}
      />
    </section>
  );
}

import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { AccessPage } from "./pages/Access";
import { AuditPage } from "./pages/Audit";
import { ChangesPage } from "./pages/Changes";
import { ContractsPage } from "./pages/Contracts";
import { DashboardPage } from "./pages/Dashboard";
import { ExtractionPage } from "./pages/Extraction";
import { GoldenPage } from "./pages/Golden";
import { IngestionPage } from "./pages/Ingestion";
import { LimitsPage } from "./pages/Limits";
import { RecordsPage } from "./pages/Records";
import { ReviewQueuePage } from "./pages/ReviewQueue";
import { SourcesPage } from "./pages/Sources";
import { SynonymsPage } from "./pages/Synonyms";
import { SystemStatusPage } from "./pages/SystemStatus";
import { UsagePage } from "./pages/Usage";

// One route for each entry of the menu (src/menu.ts). A page is one file in src/pages.
export function App() {
  return (
    <BrowserRouter basename="/console">
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="system" element={<SystemStatusPage />} />
          <Route path="usage" element={<UsagePage />} />
          <Route path="limits" element={<LimitsPage />} />
          <Route path="ingestion" element={<IngestionPage />} />
          <Route path="sources" element={<SourcesPage />} />
          <Route path="extraction" element={<ExtractionPage />} />
          <Route path="synonyms" element={<SynonymsPage />} />
          <Route path="records" element={<RecordsPage />} />
          <Route path="review" element={<ReviewQueuePage />} />
          <Route path="golden" element={<GoldenPage />} />
          <Route path="changes" element={<ChangesPage />} />
          <Route path="contracts" element={<ContractsPage />} />
          <Route path="audit" element={<AuditPage />} />
          <Route path="access" element={<AccessPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

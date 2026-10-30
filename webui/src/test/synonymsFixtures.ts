import type { CandidateList } from "../api/synonyms";

export const CANDIDATES: CandidateList = {
  items: [
    { candidate_id: "c1", method: "cooccurrence", canonical: "経費精算", synonyms: ["立替精算", "旅費精算"], status: "candidate", decided_by: null, decided_at: null },
    { candidate_id: "c2", method: "abbreviation", canonical: "株式会社サンプル", synonyms: ["サンプル社"], status: "approved", decided_by: "admin", decided_at: "2026-10-01T00:00:00+00:00" },
  ],
  counts: { candidate: 1, approved: 1 },
  total: 2,
};

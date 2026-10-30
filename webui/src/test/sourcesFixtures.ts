import type { SourcePlan, SourceRow } from "../api/sources";

export const SHA = "a".repeat(64);

export const FOLDER: SourceRow = {
  source_id: "policies",
  tenant_id: "default",
  kind: "folder",
  collection: "src-policies",
  schedule_minutes: 0,
  enabled: true,
  state: { last_scan_at: "2026-01-05T09:00:00+00:00", last_apply_at: null },
  pending: { added: 2, modified: 1, deleted: 0 },
  guard: { hold: false, reasons: [] },
};

export const HELD: SourceRow = {
  ...FOLDER,
  source_id: "manuals",
  collection: "src-manuals",
  pending: { added: 0, modified: 0, deleted: 12 },
  guard: { hold: true, reasons: ["mass_delete"] },
};

export const SHAREPOINT: SourceRow = {
  ...FOLDER,
  source_id: "portal",
  kind: "sharepoint",
  collection: "src-portal",
  enabled: false,
  pending: null,
  guard: null,
  state: { last_error: "integration_off:switch_off", last_scan_at: "2026-01-05T09:00:00+00:00" },
};

export const PLAN: SourcePlan = {
  added: ["a/new1.pdf", "a/new2.pdf"],
  modified: ["a/changed.docx"],
  deleted: [],
  conflicts: { "same.pdf": ["x/same.pdf", "y/same.pdf"] },
  counts: { added: 2, modified: 1, deleted: 0 },
  applied_files: 10,
  total_files: 12,
  plan_sha256: SHA,
  guard: { hold: false, reasons: [] },
};

export const HELD_PLAN: SourcePlan = {
  ...PLAN,
  added: [],
  modified: [],
  deleted: Array.from({ length: 12 }, (_, i) => `old/${i}.pdf`),
  counts: { added: 0, modified: 0, deleted: 12 },
  guard: { hold: true, reasons: ["mass_delete"] },
};

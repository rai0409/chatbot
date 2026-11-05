import type { QaList, QaRecord, QaRow } from "../api/approvedQa";

export const ROWS: QaRow[] = [
  { qa_id: "qa-1", tenant_id: "tenant_a", question: "申請期限はいつですか？", approved_answer: "毎月末日です。", status: "draft", tags: ["手続き"], aliases: ["期限は？"], citations: 1, updated_at: "2026-11-01T09:00:00+00:00", updated_by: "山田太郎", approved_by: null },
  { qa_id: "qa-2", tenant_id: "tenant_a", question: "解約の方法を教えてください", approved_answer: "設定の画面から解約できます。", status: "approved", tags: [], aliases: [], citations: 2, updated_at: "2026-11-02T09:00:00+00:00", updated_by: "佐藤次郎", approved_by: "佐藤次郎" },
];

export const LIST: QaList = { total: 2, counts: { draft: 1, approved: 1, rejected: 0 }, rows: ROWS, enabled: true, require_approver: false, can_see_all: true };

export const HEALTH = { total: 2, by_status: { draft: 1, approved: 1, rejected: 0 }, errors: [], unreadable_lines: 0, enabled: true, detail_hidden: false };

export const RECORD: QaRecord = {
  qa_id: "qa-1", tenant_id: "tenant_a", question: "申請期限はいつですか？", approved_answer: "毎月末日です。", status: "draft", tags: ["手続き"],
  proposed_aliases: ["期限は？"], approved_citations: [{ source_doc: "manual.pdf", source_pages: [2], title: "利用案内" }], doc_version: "v1", updated_at: "2026-11-01T09:00:00+00:00",
};

export const HISTORY = [{ at: "2026-11-01T09:00:00+00:00", action: "create", by: "山田太郎", reason: "新規", status_before: null, status_after: "draft" }];

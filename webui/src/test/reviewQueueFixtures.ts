import type { ReviewItem, ReviewItemsResponse } from "../api/reviewQueue";

export const REVIEW_ITEM: ReviewItem = {
  review_id: "rv_001",
  priority: "high",
  status: "open",
  tenant_id: "default",
  user_query: "経費精算の締め日はいつですか",
  answer_mode: "abstain",
  confidence_route: "low",
  decision_route: "abstain_low_confidence",
  candidate_ids: ["faq_1", "faq_2"],
  selected_candidate_id: "faq_1",
  feedback_type: "bad",
  bad_reason: "回答が古い",
  created_at: "2026-10-01T09:00:00+09:00",
  source: "feedback",
  reasons: ["bad_feedback", "abstained"],
};

export const REVIEW_ITEM_2: ReviewItem = {
  ...REVIEW_ITEM,
  review_id: "rv_002",
  priority: "low",
  user_query: "出張の手配方法を教えてください",
  selected_candidate_id: null,
  candidate_ids: [],
  reasons: [],
};

export const REVIEW_RESPONSE: ReviewItemsResponse = {
  items: [REVIEW_ITEM, REVIEW_ITEM_2],
  total_loaded: 5,
  returned_count: 2,
  skipped_malformed_lines: 0,
  source_path: "runs/review/review_queue.jsonl",
};

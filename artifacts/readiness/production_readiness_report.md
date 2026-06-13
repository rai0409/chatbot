# Production Readiness Report

Generated at: `2026-06-13T06:51:38.601896+00:00`

## Decision: `needs_review`

### Reasons
- static_safety_checks_passed_with_review_items

### Blockers
- No critical blockers detected by static checks.

### Warnings
- admin_auth_env_requires_production_configuration
- feature_rerank_promotion_decision_missing
- feature_rerank_promotion_decision_missing_optional
- generated_knowledge_manifest_missing
- generated_knowledge_manifest_missing_optional
- git_worktree_dirty

## Safety Checks

| Check | Status |
| --- | --- |
| `admin_auth_default_disabled` | `True` |
| `admin_auth_guard_present` | `True` |
| `api_auth_guard_present` | `True` |
| `api_key_tenant_authorization_present` | `True` |
| `approved_similar_candidate_only_present` | `True` |
| `citation_source_metadata_helper_present` | `True` |
| `feature_rerank_promotion_gate_present` | `True` |
| `knowledge_manifest_helper_present` | `True` |
| `product_profiles_present` | `True` |
| `production_rerank_not_enabled_by_default` | `True` |
| `rate_limit_guard_present` | `True` |
| `readiness_smoke_script_present` | `True` |
| `security_operations_doc_present` | `True` |
| `similar_auto_answer_disabled` | `True` |
| `tenant_mapping_present` | `True` |
| `tenant_preview_wiring_present` | `True` |

## Product Profiles
Available profiles: `default, dev_debug, evaluation, pilot_high_accuracy, production_low_cost, production_safe`
- `production_safe`: present=`True`, runtime_serving=`True`, similar_auto_answer=`False`, enabled_steps=`['audit', 'feature_rerank', 'feedback', 'metrics', 'review_queue']`
- `production_low_cost`: present=`True`, runtime_serving=`True`, similar_auto_answer=`False`, enabled_steps=`['audit', 'feature_rerank', 'feedback', 'metrics', 'review_queue']`
- `pilot_high_accuracy`: present=`True`, runtime_serving=`True`, similar_auto_answer=`False`, enabled_steps=`['audit', 'combined_rerank', 'feature_rerank', 'feedback', 'feedback_preview_rerank', 'metrics', 'review_queue']`
- `evaluation`: present=`True`, runtime_serving=`False`, similar_auto_answer=`False`, enabled_steps=`['combined_rerank', 'debug_comparison', 'feature_rerank', 'feedback_preview_rerank', 'metrics']`
- `dev_debug`: present=`True`, runtime_serving=`False`, similar_auto_answer=`False`, enabled_steps=`['audit', 'combined_rerank', 'debug_comparison', 'feature_rerank', 'feedback', 'feedback_preview_rerank', 'metrics', 'review_queue']`

## Tenant Mapping
Mapping present: `True`, default profile: `production_safe`, unknown policy: `default_profile`
Tenants: active=`1`, disabled=`0`, pilot=`0`, total=`1`

## Admin Auth
Helper present: `True`
Protected routes detected: `3`
Env vars: `['ADMIN_AUTH_ENABLED', 'ADMIN_AUTH_TOKEN']`
Default mode: `disabled unless ADMIN_AUTH_ENABLED is truthy`
Production guidance: Set ADMIN_AUTH_ENABLED=true and configure a non-empty ADMIN_AUTH_TOKEN before exposing admin routes.

## Security Operations
API auth helper present: `True`
Protected POST routes detected: `5`
API-key tenant authorization present: `True`
Rate limiter present: `True` (default off: `True`)
Security operations runbook present: `True`
Env vars: `['API_AUTH_ENABLED', 'API_AUTH_KEYS', 'API_AUTH_TENANT_MAP', 'RATE_LIMIT_ENABLED', 'RATE_LIMIT_REQUESTS_PER_MINUTE']`
Production guidance: Set API_AUTH_ENABLED=true with non-empty API_AUTH_KEYS, configure API_AUTH_TENANT_MAP for multi-tenant deployments, and enable RATE_LIMIT_ENABLED=true before external exposure. See docs/security_operations.md for rotation and secrets handling.

## Knowledge And Citation Metadata
Knowledge helper=`True`, builder=`True`, docs=`True`, generated manifest=`False`
Source metadata helper=`True`, approved QA extended metadata=`True`, product contract normalization=`True`

## Rerank Promotion
Gate present=`True`, decision artifact present=`False`, runtime enabled=`False`, production enabled=`False`

## Known Blockers
- DB persistence / tenant isolation not fully production-grade
- /chat tenant runtime wiring not enabled
- production rerank not enabled
- rollback workflow still manual/config-based
- generated manifest may need deployment review
- end-to-end deployed server smoke is manual

## Recommended Next Steps
- run scripts/product_readiness_smoke.sh
- review docs/production_readiness_checklist.md
- generate/review knowledge manifest
- review tenant mapping
- enable ADMIN_AUTH_ENABLED=true with ADMIN_AUTH_TOKEN in production
- enable API_AUTH_ENABLED=true and RATE_LIMIT_ENABLED=true before external exposure (see docs/security_operations.md)
- keep similar auto-answer disabled
- run limited pilot via /chat/product-preview before /chat runtime wiring

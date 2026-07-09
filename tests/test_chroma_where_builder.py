from __future__ import annotations

import config
from rag_core import retrieval
from rag_core.retrieval import _build_base_where, _to_chroma_where


def _flatten(where):
    if not where:
        return {}
    if "$and" in where:
        flat = {}
        for clause in where["$and"]:
            flat.update(clause)
        return flat
    return dict(where)


# --- _to_chroma_where converter (boundary form) ----------------------------


def test_empty_where_becomes_none():
    # Empty filter remains "no filter" (Chroma rejects a zero-key where dict).
    assert _to_chroma_where({}) is None
    assert _to_chroma_where(None) is None


def test_single_condition_passes_through():
    assert _to_chroma_where({"searchable": 1}) == {"searchable": 1}
    assert _to_chroma_where({"tenant_id": "tenant_a"}) == {"tenant_id": "tenant_a"}


def test_tenant_and_searchable_emitted_as_and():
    out = _to_chroma_where({"searchable": 1, "tenant_id": "tenant_a"})
    assert "$and" in out
    assert {"searchable": 1} in out["$and"]
    assert {"tenant_id": "tenant_a"} in out["$and"]
    # Every original clause is a single-key object (Chroma-safe).
    assert all(len(c) == 1 for c in out["$and"])
    # No condition added or removed.
    assert _flatten(out) == {"searchable": 1, "tenant_id": "tenant_a"}


def test_three_conditions_all_preserved_as_single_key_clauses():
    flat = {"type": {"$in": ["faq"]}, "searchable": 1, "tenant_id": "tenant_a"}
    out = _to_chroma_where(flat)
    assert "$and" in out
    assert all(len(c) == 1 for c in out["$and"])
    assert _flatten(out) == flat


# --- Tenant isolation is not weakened by the conversion --------------------


def test_non_default_tenant_clause_survives_conversion(monkeypatch):
    # _build_base_where keeps the flat form (default: searchable on), so a
    # non-default tenant produces a multi-key where; conversion must keep the
    # strict tenant_id equality clause.
    monkeypatch.setattr(config, "IGNORE_SEARCHABLE", False)
    flat = _build_base_where(tenant_id="tenant_a")
    assert flat.get("tenant_id") == "tenant_a"  # internal flat form unchanged
    chroma = _to_chroma_where(flat)
    assert _flatten(chroma)["tenant_id"] == "tenant_a"
    assert _flatten(chroma).get("searchable") == 1


def test_default_tenant_adds_no_tenant_clause(monkeypatch):
    monkeypatch.setattr(config, "IGNORE_SEARCHABLE", False)
    flat = _build_base_where(tenant_id="default")
    assert "tenant_id" not in flat
    chroma = _to_chroma_where(flat)
    assert "tenant_id" not in _flatten(chroma)


def test_build_base_where_internal_form_is_still_flat(monkeypatch):
    # The keyword path and _meta_matches_where depend on the flat dict form;
    # the builder itself must not change to $and.
    monkeypatch.setattr(config, "IGNORE_SEARCHABLE", False)
    flat = _build_base_where(tenant_id="tenant_a")
    assert "$and" not in flat
    assert flat == {"searchable": 1, "tenant_id": "tenant_a"}


def test_meta_matches_where_still_consumes_flat_form():
    # Regression guard: the in-memory keyword filter still works on the flat
    # dict (it is never handed the $and form).
    flat = {"searchable": 1, "type": {"$in": ["faq"]}}
    assert retrieval._meta_matches_where({"searchable": 1, "type": "faq"}, flat) is True
    assert retrieval._meta_matches_where({"searchable": 0, "type": "faq"}, flat) is False
    assert retrieval._meta_matches_where({"searchable": 1, "type": "other"}, flat) is False

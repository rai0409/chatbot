import math

import pytest

import config
from rag_core import answer_cache, knob_registry


def test_every_registered_knob_exists_in_config_with_a_valid_current_value():
    for name in knob_registry.names():
        assert hasattr(config, name) or name in knob_registry.ENV_KNOBS, name
        assert knob_registry.check_value(name, knob_registry.current(name)) == knob_registry.current(name), name


def test_registry_entries_are_well_formed():
    names = [k.name for k in knob_registry.REGISTRY]
    assert len(names) == len(set(names))
    for k in knob_registry.REGISTRY:
        assert k.kind in {"bool", "int", "float"} and k.risk in {"low", "high"} and k.description
        if k.kind == "bool":
            assert k.lo is None and k.hi is None
        else:
            assert k.lo is not None and k.hi is not None and k.lo < k.hi
    assert {k.name for k in knob_registry.REGISTRY if k.risk == "high"} == {
        "RAG_HARD_MAX_DIST", "RAG_SOFT_DIST_OTHER", "RAG_SOFT_DIST_RESET", "RAG_SOFT_DIST_CHANGE",
    }


def test_every_knob_changes_the_answer_cache_key():
    assert set(knob_registry.names()) <= set(answer_cache._SETTING_NAMES) | set(answer_cache._SETTING_ENV_NAMES)


@pytest.mark.parametrize(
    "name, value, expected",
    [
        ("PROFILE_BOOST_ENABLED", True, True),
        ("HYBRID_RRF_K", 60, 60),
        ("HYBRID_RRF_K", 60.0, 60),
        ("PROFILE_BOOST_MAX_DELTA", 0, 0.0),
        ("PROFILE_BOOST_MAX_DELTA", 0.2, 0.2),
        ("RAG_HARD_MAX_DIST", 0.95, 0.95),
    ],
)
def test_valid_values_are_returned_in_their_type(name, value, expected):
    result = knob_registry.check_value(name, value)
    assert result == expected and type(result) is type(expected)


@pytest.mark.parametrize(
    "name, value",
    [
        ("PROFILE_BOOST_ENABLED", 1),
        ("PROFILE_BOOST_ENABLED", "true"),
        ("HYBRID_RRF_K", 60.5),
        ("HYBRID_RRF_K", True),
        ("HYBRID_RRF_K", "60"),
        ("HYBRID_RRF_K", 0),
        ("HYBRID_RRF_K", 201),
        ("PROFILE_BOOST_MAX_DELTA", -0.01),
        ("PROFILE_BOOST_MAX_DELTA", 0.21),
        ("PROFILE_BOOST_MAX_DELTA", math.nan),
        ("PROFILE_BOOST_MAX_DELTA", math.inf),
        ("RAG_HARD_MAX_DIST", None),
    ],
)
def test_invalid_values_are_refused(name, value):
    with pytest.raises(knob_registry.KnobError):
        knob_registry.check_value(name, value)


def test_an_unregistered_name_is_refused_everywhere():
    for call in (
        lambda: knob_registry.knob("DATABASE_URL"),
        lambda: knob_registry.check_value("DATABASE_URL", 1),
        lambda: knob_registry.current("DATABASE_URL"),
        lambda: knob_registry.check_change({"HYBRID_RRF_K": 60, "DATABASE_URL": 1}),
    ):
        with pytest.raises(knob_registry.KnobError, match="not a registered knob"):
            call()


def test_check_change_is_all_or_nothing_and_needs_a_knob():
    assert knob_registry.check_change({"HYBRID_RRF_K": 40, "PROFILE_BOOST_ENABLED": True}) == {
        "HYBRID_RRF_K": 40, "PROFILE_BOOST_ENABLED": True,
    }
    with pytest.raises(knob_registry.KnobError):
        knob_registry.check_change({"HYBRID_RRF_K": 40, "HYBRID_DENSE_WEIGHT": 9})
    with pytest.raises(knob_registry.KnobError, match="at least one"):
        knob_registry.check_change({})


def test_snapshot_reads_the_current_settings(monkeypatch):
    monkeypatch.setattr(config, "HYBRID_RRF_K", 77)
    snapshot = knob_registry.snapshot()
    assert snapshot["HYBRID_RRF_K"] == 77 and set(snapshot) == set(knob_registry.names())

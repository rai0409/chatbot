"""S7: the circuit breakers shared between workers through Redis, and what happens when Redis is down."""
import json
import threading

import pytest

from rag_core import circuit_breaker as cb
from rag_core.circuit_breaker import CLOSED, HALF_OPEN, OPEN, CircuitBreaker, FallbackStore, MemoryStore
from webapi import breaker_store


class FakeRedis:
    """Just enough of redis-py for the store: get, and a pipeline with watch/multi/execute that detects a change in between."""

    def __init__(self):
        self.data, self.versions, self.expiry = {}, {}, {}
        self.lock = threading.Lock()
        self.conflicts_to_inject = 0
        self.down = False

    def _check(self):
        if self.down:
            raise ConnectionError("redis is down")

    def get(self, key):
        self._check()
        return self.data.get(key)

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis):
        self.redis, self.watched, self.seen, self.queued = redis, None, None, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def watch(self, key):
        self.redis._check()
        self.watched, self.seen = key, self.redis.versions.get(key, 0)

    def get(self, key):
        return self.redis.get(key)

    def multi(self):
        pass

    def set(self, key, value, ex=None):
        self.queued.append((key, value, ex))

    def execute(self):
        from redis.exceptions import WatchError

        with self.redis.lock:
            if self.redis.conflicts_to_inject > 0:
                self.redis.conflicts_to_inject -= 1
                raise WatchError("changed meanwhile")
            if self.redis.versions.get(self.watched, 0) != self.seen:
                raise WatchError("changed meanwhile")
            for key, value, ex in self.queued:
                self.redis.data[key] = value
                self.redis.versions[key] = self.redis.versions.get(key, 0) + 1
                self.redis.expiry[key] = ex


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def _store(redis):
    return breaker_store.RedisStore(lambda: redis)


def _breaker(store, clock, **kw):
    return CircuitBreaker("llm:local", threshold=kw.get("threshold", 3), open_seconds=60, probes=kw.get("probes", 1), store=store, clock=clock)


def test_two_workers_share_one_state():
    redis, clock = FakeRedis(), Clock()
    a, b = _breaker(_store(redis), clock, threshold=2), _breaker(_store(redis), clock, threshold=2)  # two workers, the same Redis
    a.record_failure()
    b.record_failure()  # the second failure is counted with the first although another worker saw it
    assert a.state() == OPEN and b.state() == OPEN and not a.allow() and not b.allow()
    assert json.loads(redis.data[breaker_store.PREFIX + "llm:local"])["state"] == OPEN
    assert redis.expiry[breaker_store.PREFIX + "llm:local"] == breaker_store.TTL_SECONDS


def test_the_single_trial_of_a_half_open_breaker_is_taken_by_one_worker_only():
    redis, clock = FakeRedis(), Clock()
    a, b = _breaker(_store(redis), clock, threshold=1), _breaker(_store(redis), clock, threshold=1)
    a.record_failure()
    clock.now += 61
    assert a.state() == HALF_OPEN and b.state() == HALF_OPEN
    assert [a.allow(), b.allow()].count(True) == 1  # one trial for everybody
    a.record_success()
    assert b.state() == CLOSED and b.allow()  # and its result is seen by the other worker


def test_a_conflicting_change_is_read_again_and_not_lost():
    redis, clock = FakeRedis(), Clock()
    breaker = _breaker(_store(redis), clock, threshold=3)
    redis.conflicts_to_inject = 2  # another worker changed the key twice while this one was working
    breaker.record_failure()
    assert json.loads(redis.data[breaker_store.PREFIX + "llm:local"])["failures"] == 1
    redis.conflicts_to_inject = 99  # a key that never settles: it is an error, not an endless loop
    with pytest.raises(RuntimeError, match="too many concurrent changes"):
        _store(redis).update("llm:x", cb.default_state(), lambda s: (s, None))


def test_a_closed_breaker_writes_nothing_on_each_success():
    redis, clock = FakeRedis(), Clock()
    breaker = _breaker(_store(redis), clock)
    for _ in range(20):
        breaker.record_success()
    assert redis.data == {}  # no write to Redis for a healthy endpoint


def test_without_redis_the_worker_keeps_its_own_breaker_and_the_primary_is_used_again_when_it_is_back():
    redis, clock, errors = FakeRedis(), Clock(), []
    store = FallbackStore(_store(redis), MemoryStore(), on_error=errors.append)
    breaker = _breaker(store, clock, threshold=2)
    redis.down = True
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state() == OPEN and not breaker.allow()  # protected by its own memory
    assert store.fell_back >= 3 and isinstance(errors[0], ConnectionError)
    redis.down = False
    other = _breaker(store, clock, threshold=2)
    assert other.state() == CLOSED  # Redis knows nothing of what happened while it was away
    other.record_failure()
    other.record_failure()
    assert json.loads(redis.data[breaker_store.PREFIX + "llm:local"])["state"] == OPEN  # the shared state is written again


def test_a_broken_value_in_redis_is_a_fresh_state_and_listeners_hear_about_changes():
    redis, clock, heard = FakeRedis(), Clock(), []
    redis.data[breaker_store.PREFIX + "llm:local"] = "{ not json"
    cb.add_listener(lambda key, old, new, at: heard.append((old, new)))
    breaker = _breaker(_store(redis), clock, threshold=1)
    assert breaker.state() == CLOSED
    breaker.record_failure()
    clock.now += 61
    breaker.allow()
    breaker.record_success()
    assert heard == [(CLOSED, OPEN), (OPEN, HALF_OPEN), (HALF_OPEN, CLOSED)]


def test_install_follows_the_backend_setting(monkeypatch):
    monkeypatch.delenv("BREAKER_BACKEND", raising=False)
    assert breaker_store.install() is False and isinstance(cb.current_store(), MemoryStore)
    monkeypatch.setenv("BREAKER_BACKEND", "redis")
    assert breaker_store.install() is True and isinstance(cb.current_store(), FallbackStore)
    assert isinstance(cb.current_store().primary, breaker_store.RedisStore)


def test_the_breaker_backend_is_part_of_the_readiness_check(monkeypatch):
    from webapi import state_backend

    assert "BREAKER_BACKEND" in state_backend._REDIS_USERS
    monkeypatch.setenv("BREAKER_BACKEND", "redis")
    monkeypatch.setattr(state_backend, "redis_client", lambda: type("R", (), {"ping": lambda self: True})())
    health = state_backend.health()
    assert health["redis"]["ok"] is True and "BREAKER_BACKEND" in health["redis"]["used_by"]
